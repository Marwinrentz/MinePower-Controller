"""Erststart-Logik: Schema, TimescaleDB-Hypertable, Default-Einstellungen,
Headless-Config-Import und Demo-Modus."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import func, select, text

from .config import get_settings
from .db import Base, async_session, engine
from .models import Device, Setting, User
from .security import hash_password

log = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict[str, dict] = {
    "regulation": {
        # 10 s vertragen alle Treiber; Bedienbefehle wirken ohnehin sofort.
        "interval_s": 10,
        "grid_target_w": 0,
        "house_limit_a": 35,
        "house_phases": 3,
        # Batteriekapazität, falls das Gerät sie nicht meldet (0 = unbekannt)
        "battery_capacity_kwh": 0,
        # Batterie – ein Konzept (siehe core/battery_policy.py):
        # Die EINE Reserve. Wird in den Wechselrichter geschrieben (soweit er
        # sie annimmt), den Rest hält MinePower selbst.
        "battery_reserve_soc": 20,
        # 'Batterie zuerst bis …": bis hierhin bekommt der Speicher Sonne und
        # günstigen Netzstrom vor Auto und Warmwasser. 100 = immer zuerst.
        "battery_priority_soc": 100,
        # Experte: ab diesem Ladestand darf der Speicher Auto/Warmwasser
        # mitversorgen. 0 = nie.
        "battery_ev_support_soc": 0,
        # Ab wie viel Entladeleistung eine laufende Überschuss-Last hart
        # abschaltet. Darunter wird nur abgeregelt.
        "battery_drain_limit_w": 250,
        "use_forecast": True,
        # Netzladen der Batterie: eigener, standardmäßig AUSGESCHALTETER
        # Schalter – nichts anderes lädt den Speicher aus dem Netz.
        "battery_grid_charge_enabled": False,
        "battery_grid_charge_soc": 50,
        # Nur laden, wenn die Prognose sagt, dass die Sonne es nicht schafft.
        "battery_grid_charge_forecast": True,
        # Experte: Speicher auch nicht für Sofort-Laden, Boost und
        # Geräteprogramme hergeben (Preis-Lasten sind immer gesperrt).
        "battery_protect_other_loads": False,
        # Höchster Netzbezug beim Laden aus dem Netz (W). 0 = aus dem
        # Hausanschluss abgeleitet (house_limit_a × 230 V × 3).
        "grid_charge_max_w": 0,
        # Manuelle Batteriebefehle (Dashboard): Vorgabeleistung, längste Dauer.
        "battery_manual_power_w": 3000,
        "battery_manual_max_min": 240,
        # Eingriffe am Auto enden spätestens nach dieser Zeit (h).
        "override_stop_h": 4,
        "override_fast_h": 12,
        # Schlafendes, angestecktes Auto bei Überschuss wecken (alle 30 min max.)
        "vehicle_wake_enabled": True,
    },
    "priority": {"order": []},
    "tariff": {
        "provider": "none",
        # Schwelle, ab der aus dem Netz geladen wird – verglichen mit dem
        # ENDPREIS, nicht mit dem Börsenpreis (siehe core/pricing.py).
        "grid_charge_limit_ct": 15,
        "cheap_limit_ct": 15,  # alter Name, bleibt als Rückfall
        # Aufschläge zwischen Börsen- und Endpreis. Ohne sie vergleicht die
        # Schwelle gegen einen Preis, den niemand zahlt.
        "grid_fees_ct": 0,
        "levies_ct": 0,
        "supplier_margin_ct": 0,
        "vat_pct": 0,
        "charge_on_negative": True,
        # Automatische Grenze: immer der tiefste Preis der naechsten 24 h.
        "auto_limit": False,
        "auto_limit_window_h": 2,
        "auto_limit_tolerance_ct": 0,
    },
    "forecast": {"provider": "none"},
    "notifications": {"ntfy_url": "", "telegram_token": "", "telegram_chat_id": "", "email": "",
                      "webhook_url": ""},
    "ui": {"default_language": "de"},
}


async def ensure_jwt_secret() -> None:
    """Plug & Play: Ist kein JWT_SECRET gesetzt, wird beim Erststart eines
    erzeugt und in der DB persistiert – Neustarts invalidieren also keine
    Sessions. Ein explizit gesetztes ENV-Secret hat immer Vorrang."""
    import secrets as pysecrets

    cfg = get_settings()
    if cfg.jwt_secret and cfg.jwt_secret != "insecure-dev-secret-change-me":
        return
    async with async_session() as session:
        row = await session.get(Setting, "system")
        value = dict(row.value) if row else {}
        if not value.get("jwt_secret"):
            value["jwt_secret"] = pysecrets.token_hex(32)
            if row is None:
                session.add(Setting(key="system", value=value))
            else:
                row.value = value
            await session.commit()
            log.info("JWT-Secret automatisch erzeugt und persistiert")
        cfg.jwt_secret = value["jwt_secret"]


async def maybe_restore_from_backup() -> bool:
    """Springt ein, wenn die Datenbank leer, aber eine lokale Sicherung
    vorhanden ist – der Fall nach einem Stromausfall, bei dem die
    Postgres-Datendatei neu initialisiert werden musste (siehe
    services/backup.py). Eine frische Erstinstallation hat noch keine
    Sicherung und bleibt unangetastet; import_headless_config und
    setup_demo_mode greifen wie gehabt nur, wenn danach immer noch keine
    Geräte da sind."""
    from .services import backup

    async with async_session() as session:
        devices = await session.scalar(select(func.count(Device.id)))
        users = await session.scalar(select(func.count(User.id)))
        if devices or users:
            return False

    path = backup.latest_backup()
    if path is None:
        return False

    count = await backup.restore(path)
    log.warning(
        "Datenbank war leer – lokale Sicherung %s automatisch eingespielt (%d Geräte). "
        "Vermutlich musste das Postgres-Datenverzeichnis nach einem Stromausfall neu "
        "angelegt werden.", path.name, count,
    )
    # Dazu der Verlauf aus der letzten Datensicherung (Stundenwerte,
    # Ladevorgänge, Ereignisse) – ein Fehler dabei darf den Start nicht
    # verhindern, die Anlage regelt auch ohne Verlauf.
    data_path = backup.latest_data_backup()
    if data_path is not None:
        try:
            counts = await backup.restore_data(data_path)
            log.warning("Verlauf aus %s eingespielt: %d Stundenwerte, %d Ladevorgänge, %d Ereignisse",
                        data_path.name, counts["measurements"], counts["sessions"], counts["events"])
        except Exception as exc:  # noqa: BLE001
            log.error("Verlauf aus %s nicht eingespielt: %s", data_path.name, exc)
    return True


#: Gibt es das Stundenaggregat ``measurements_hourly`` (TimescaleDB)? Verlauf
#: und Kennzahlen über lange Zeiträume lesen daraus; ohne es bleiben nur die
#: Rohdaten (siehe api/history.py, api/statistics.py).
HOURLY_VIEW = False

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"
#: Tabellen, die vor einer Schema-Migration roh gesichert werden
_CONFIG_TABLES = ("users", "api_tokens", "devices", "settings", "vehicles", "schedules")


async def init_db() -> None:
    """Schema anlegen und migrieren, danach die Zeitreihen-Ausstattung.

    1. Steht eine Schema-Migration an und die Datenbank enthält schon Daten,
       wird vorher eine Rohsicherung der Konfigurationstabellen geschrieben.
    2. ``create_all`` legt fehlende Tabellen an (frische Installation).
    3. Alembic hebt das Schema auf den aktuellen Stand und merkt ihn sich in
       ``alembic_version``. Alle Revisionen prüfen selbst, was schon da ist,
       und ergänzen nur – ältere Programmversionen laufen mit dem neueren
       Schema weiter.
    4. TimescaleDB: Hypertable, Aufbewahrung, Kompression, Stundenaggregat –
       jeder Schritt einzeln abgesichert, damit ein fehlender Baustein (reines
       PostgreSQL, Apache-Lizenz ohne Kompression) nichts anderes mitreißt.
    """
    current, head = await _schema_revisions()
    if current != head and await _has_data():
        await _backup_before_migration(current, head)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_schema(current, head)
    await setup_timeseries()


async def _schema_revisions() -> tuple[str | None, str | None]:
    """(Stand der Datenbank, Stand dieses Programms)."""
    head: str | None = None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config()
        cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        head = ScriptDirectory.from_config(cfg).get_current_head()
    except Exception as exc:  # noqa: BLE001 – ohne Alembic-Ordner bleibt der Rückfall
        log.warning("Alembic-Revisionen nicht lesbar: %s", exc)
    current: str | None = None
    try:
        async with engine.connect() as conn:
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    except Exception:  # noqa: BLE001 – Tabelle fehlt: Installation vor 3.3.0 oder frisch
        current = None
    return current, head


async def _has_data() -> bool:
    try:
        async with engine.connect() as conn:
            users = (await conn.execute(text("SELECT count(*) FROM users"))).scalar() or 0
            devices = (await conn.execute(text("SELECT count(*) FROM devices"))).scalar() or 0
            return bool(users or devices)
    except Exception:  # noqa: BLE001 – Tabellen fehlen: frische Datenbank
        return False


async def _backup_before_migration(current: str | None, head: str | None) -> None:
    """Konfigurationstabellen roh als JSON sichern, bevor das Schema angefasst
    wird. Bewusst ohne die Modelle: Die beschreiben schon das neue Schema und
    könnten das alte nicht lesen."""
    from datetime import datetime, timezone

    from .services import backup

    dump: dict = {"kind": "pre-migration", "from_revision": current, "to_revision": head,
                  "created_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    try:
        async with engine.connect() as conn:
            for table in _CONFIG_TABLES:
                try:
                    rows = (await conn.execute(text(f"SELECT * FROM {table}"))).mappings().all()
                except Exception:  # noqa: BLE001 – Tabelle gibt es in der alten Version nicht
                    await conn.rollback()
                    continue
                dump["tables"][table] = [dict(r) for r in rows]
        path = await backup.write_named(f"pre-migration-{current or 'base'}-to-{head}", dump)
        log.info("Sicherung vor Schema-Migration geschrieben: %s", path.name)
    except Exception as exc:  # noqa: BLE001
        # Die Migrationen ergänzen nur; ohne Sicherung weiterzumachen ist
        # besser als eine Anlage, die nicht mehr startet.
        log.error("Sicherung vor Schema-Migration fehlgeschlagen: %s", exc)


async def _migrate_schema(current: str | None, head: str | None) -> None:
    def upgrade(sync_conn) -> None:
        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        cfg.attributes["connection"] = sync_conn
        command.upgrade(cfg, "head")

    if head is None:
        async with engine.begin() as conn:
            await conn.run_sync(_add_missing_columns)
        return
    try:
        async with engine.begin() as conn:
            await conn.run_sync(upgrade)
        if current != head:
            log.info("Datenbank-Schema: %s → %s", current or "ohne Stand", head)
    except Exception as exc:  # noqa: BLE001
        if "Can't locate revision" in str(exc):
            # Ein älteres Programm an einer neueren Datenbank (Image
            # zurückgesetzt). Das Schema wächst nur, also weiterarbeiten.
            log.warning("Datenbank-Schema (%s) ist neuer als dieses Programm (%s) – läuft weiter", current, head)
        else:
            log.error("Schema-Migration fehlgeschlagen (%s) – ergänze fehlende Spalten direkt", exc)
        async with engine.begin() as conn:
            await conn.run_sync(_add_missing_columns)


async def _try(conn, sql: str, what: str, params: dict | None = None) -> bool:
    """Eine Anweisung in einem Savepoint ausführen. Ein Fehlschlag macht in
    PostgreSQL sonst die ganze Transaktion unbrauchbar – und nahm früher auf
    einer Datenbank ohne TimescaleDB das frisch angelegte Schema mit."""
    try:
        async with conn.begin_nested():
            await conn.execute(text(sql), params or {})
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("Zeitreihen: %s nicht verfügbar (%s)", what, str(exc).splitlines()[0][:160])
        return False


async def setup_timeseries() -> None:
    """TimescaleDB einrichten, soweit vorhanden. Idempotent."""
    global HOURLY_VIEW
    HOURLY_VIEW = False
    if engine.dialect.name != "postgresql":
        return
    days = max(7, int(get_settings().raw_retention_days))
    async with engine.begin() as conn:
        if not await _try(conn, "CREATE EXTENSION IF NOT EXISTS timescaledb", "TimescaleDB"):
            log.warning("TimescaleDB nicht verfügbar, nutze reines PostgreSQL (ohne Kompression und Stundenwerte)")
            return
        if not await _try(conn, "SELECT create_hypertable('measurements', 'time', if_not_exists => TRUE, "
                                "migrate_data => TRUE)", "Hypertable"):
            return
        # Rohdaten begrenzt aufbewahren; die Stundenwerte bleiben (siehe unten).
        await _try(conn, "SELECT remove_retention_policy('measurements', if_exists => TRUE)", "Aufbewahrung")
        await _try(conn, f"SELECT add_retention_policy('measurements', INTERVAL '{days} days', "
                         f"if_not_exists => TRUE)", "Aufbewahrung")
        # Rohdaten älter als sieben Tage komprimieren (typisch auf ein Zehntel).
        if await _try(conn, "ALTER TABLE measurements SET (timescaledb.compress, "
                            "timescaledb.compress_segmentby = 'source, field', "
                            "timescaledb.compress_orderby = 'time DESC')", "Kompression"):
            await _try(conn, "SELECT add_compression_policy('measurements', INTERVAL '7 days', "
                             "if_not_exists => TRUE)", "Kompression")
        log.info("TimescaleDB-Hypertable aktiv (Rohdaten %d Tage)", days)
    await _setup_hourly_view(days)


HOURLY_VIEW_SQL = """
CREATE MATERIALIZED VIEW measurements_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 hour', time) AS bucket, source, field,
       avg(value) AS avg_value, min(value) AS min_value, max(value) AS max_value,
       avg(GREATEST(value, 0)) AS avg_pos, avg(LEAST(value, 0)) AS avg_neg,
       count(*) AS samples, min(time) AS first_time, max(time) AS last_time
FROM measurements
GROUP BY bucket, source, field
WITH NO DATA
"""


async def _setup_hourly_view(retention_days: int) -> None:
    """Stundenwerte als fortlaufendes Aggregat: klein genug, um sie über
    Jahre zu behalten, während die Rohdaten nach ``retention_days`` gehen.
    Ohne sie zeigte die Jahresansicht nur die letzten 90 Tage."""
    global HOURLY_VIEW
    # Eigene Verbindung ohne Transaktion: Aggregate lassen sich nicht in
    # einem Transaktionsblock auffrischen.
    try:
        async with engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            exists = (await conn.execute(text(
                "SELECT 1 FROM timescaledb_information.continuous_aggregates "
                "WHERE view_name = 'measurements_hourly'"))).scalar()
            if not exists:
                await conn.execute(text(HOURLY_VIEW_SQL))
                # Auch die laufende Stunde liefern (Rohdaten dazumischen)
                await conn.execute(text(
                    "ALTER MATERIALIZED VIEW measurements_hourly SET (timescaledb.materialized_only = false)"))
                # Vorhandene Rohdaten einmal vollständig einrechnen.
                await conn.execute(text(
                    "CALL refresh_continuous_aggregate('measurements_hourly', NULL, now() - INTERVAL '1 hour')"))
                log.info("Stundenwerte angelegt und aus den vorhandenen Rohdaten berechnet")
            # Das Fenster der Auffrischung endet vor der Aufbewahrungsgrenze
            # der Rohdaten: Sonst würden Stundenwerte neu berechnet, deren
            # Rohdaten schon gelöscht sind – und damit selbst verschwinden.
            window = max(3, min(30, retention_days - 2))
            await conn.execute(text(
                "SELECT remove_continuous_aggregate_policy('measurements_hourly', if_exists => TRUE)"))
            await conn.execute(text(
                f"SELECT add_continuous_aggregate_policy('measurements_hourly', "
                f"start_offset => INTERVAL '{window} days', end_offset => INTERVAL '1 hour', "
                f"schedule_interval => INTERVAL '30 minutes', if_not_exists => TRUE)"))
        HOURLY_VIEW = True
    except Exception as exc:  # noqa: BLE001
        log.warning("Stundenwerte nicht verfügbar (%s) – lange Zeiträume lesen die Rohdaten",
                    str(exc).splitlines()[0][:200])


async def refresh_hourly_view() -> None:
    """Stundenwerte über den ganzen Bestand neu berechnen – nach dem
    Einspielen einer Datensicherung, bevor die Aufbewahrung die alten
    Rohwerte wieder entfernt."""
    if not HOURLY_VIEW:
        return
    try:
        async with engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text(
                "CALL refresh_continuous_aggregate('measurements_hourly', NULL, now() - INTERVAL '1 hour')"))
    except Exception as exc:  # noqa: BLE001
        log.warning("Stundenwerte nicht neu berechnet: %s", str(exc).splitlines()[0][:200])


async def wait_for_db(attempts: int = 60, delay_s: float = 5.0) -> None:
    """Auf die Datenbank warten, statt beim Start abzubrechen.

    Nach einem Stromausfall starten beide Container gleichzeitig, und
    PostgreSQL spielt erst sein WAL zurück. Bisher endete der Start dann mit
    einem Verbindungsfehler und Docker begann von vorn."""
    import asyncio

    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            if attempt > 1:
                log.info("Datenbank erreichbar (Versuch %d)", attempt)
            return
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts:
                raise
            if attempt == 1 or attempt % 6 == 0:
                log.warning("Datenbank noch nicht erreichbar (Versuch %d von %d): %s", attempt, attempts,
                            str(exc).splitlines()[0][:160])
            await asyncio.sleep(delay_s)


#: Spalten, die nach dem ersten Release dazukamen: (Tabelle, Spalte, SQL-Typ).
#: Rückfall für den Fall, dass Alembic nicht laufen kann; im Normalfall
#: ergänzen die Revisionen unter alembic/versions die Spalten.
ADDED_COLUMNS = [
    ("users", "last_seen_version", "VARCHAR(20)"),
    ("users", "password_changed_at", "TIMESTAMP WITH TIME ZONE"),
    ("users", "prefs", "JSON"),
]


def _add_missing_columns(sync_conn) -> None:
    from sqlalchemy import inspect

    inspector = inspect(sync_conn)
    for table, column, sql_type in ADDED_COLUMNS:
        if table in inspector.get_table_names() and column not in {c["name"] for c in inspector.get_columns(table)}:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
            log.info("Spalte ergänzt: %s.%s", table, column)


async def track_version() -> str | None:
    """Laufende Version merken; liefert die zuvor laufende.

    Bis 2.17 gab es keine Versionsablage. Hat eine solche Installation
    schon Konten, sehen diese beim ersten Aufruf die Änderungen seit 2.17.0.
    Eine Neuinstallation zeigt kein Hinweisfenster."""
    from . import __version__

    async with async_session() as session:
        row = await session.get(Setting, "system")
        value = dict(row.value) if row else {}
        previous = value.get("app_version")
        if previous is None:
            users = (await session.scalars(select(User).where(User.last_seen_version.is_(None)))).all()
            for user in users:
                user.last_seen_version = "2.17.0"
        if previous != __version__:
            value["app_version"] = __version__
            if row is None:
                session.add(Setting(key="system", value=value))
            else:
                row.value = value
            if previous:
                log.info("Update erkannt: %s → %s", previous, __version__)
        await session.commit()
    return previous


async def seed_defaults() -> None:
    """Fehlende Einstellungen ergänzen – bestehende NIE überschreiben.

    Auch innerhalb bereits vorhandener Abschnitte: Wenn ein Update neue
    Schlüssel einführt (z. B. das adaptive Totband), bekommen bestehende
    Installationen deren Standardwerte, ohne dass eigene Anpassungen
    verlorengehen."""
    async with async_session() as session:
        rows = {s.key: s for s in (await session.scalars(select(Setting))).all()}
        for key, defaults in DEFAULT_SETTINGS.items():
            row = rows.get(key)
            if row is None:
                session.add(Setting(key=key, value=defaults))
                continue
            current = dict(row.value or {})
            missing = {k: v for k, v in defaults.items() if k not in current}
            if missing:
                row.value = {**current, **missing}
                log.info("Neue Standardwerte für '%s' ergänzt: %s", key, sorted(missing))
        await session.commit()


#: Stand des Einstellungsschemas. Jede Migration hebt ihn um eins.
SCHEMA_VERSION = 2


async def migrate_settings() -> None:
    """Bestehende Einstellungen auf das aktuelle Modell heben – ohne dass
    eine Absicht verloren geht (Schema 2 = MinePower 2.16).

    * Regelung: ein Batterie-Konzept statt drei Schalter, eine Reserve,
      eine Preisgrenze, Takt höchstens 10 s, Totband/Glättung automatisch
      (Details: core/regulation.migrate_regulation).
    * Kette: Der Speicher steht nicht mehr darin (sein Platz ist
      'Batterie zuerst bis …").
    * Geräte: Preisgrenzen je Gerät entfallen (eine Grenze im Tarif);
      die Reserve je Batteriegerät entfällt (eine Reserve); die Reserve
      wird standardmäßig in den Wechselrichter geschrieben.

    Vor der Migration wird der alte Stand unter 'schema" mitgesichert, damit
    er im Notfall nachvollziehbar bleibt."""
    from .core.regulation import migrate_regulation

    async with async_session() as session:
        rows = {s.key: s for s in (await session.scalars(select(Setting))).all()}
        schema = rows.get("schema")
        version = int((schema.value or {}).get("version", 1)) if schema else 1
        if version >= SCHEMA_VERSION:
            return
        backup: dict = {"regulation": dict((rows.get("regulation").value or {}) if rows.get("regulation") else {})}
        reg = rows.get("regulation")
        if reg is not None:
            reg.value = migrate_regulation(reg.value or {})
        devices = (await session.scalars(select(Device))).all()
        battery_ids = {d.id for d in devices if d.category == "battery"}
        prio = rows.get("priority")
        if prio is not None:
            order = list((prio.value or {}).get("order", []))
            backup["priority"] = order
            prio.value = {"order": [i for i in order if i not in battery_ids]}
        changed_devices = []
        for dev in devices:
            settings = dict(dev.settings or {})
            before = dict(settings)
            if dev.category in ("wallbox", "water_heater"):
                for key in ("price_limit_mode", "price_limit_ct", "use_battery_for_ev"):
                    settings.pop(key, None)
            if dev.category == "battery":
                settings.pop("reserve_soc", None)
                settings.pop("price_window_mode", None)
                settings["manage_reserve"] = True
            if settings != before:
                dev.settings = settings
                changed_devices.append({"id": dev.id, "before": before})
        backup["devices"] = changed_devices
        value = {"version": SCHEMA_VERSION, "migrated_from": version, "backup_v1": backup}
        if schema is None:
            session.add(Setting(key="schema", value=value))
        else:
            schema.value = value
        await session.commit()
        log.info("Einstellungen auf Schema %d migriert (%d Geräte angepasst)", SCHEMA_VERSION, len(changed_devices))


async def needs_setup() -> bool:
    async with async_session() as session:
        count = await session.scalar(select(func.count(User.id)))
        return (count or 0) == 0


async def import_headless_config() -> None:
    """Gemountete JSON-Config importieren (nur wenn DB noch leer konfiguriert ist).
    Format identisch zum GUI-Export (siehe docs/headless.md)."""
    cfg = get_settings()
    if not cfg.headless_config:
        return
    path = Path(cfg.headless_config)
    if not path.is_file():
        log.warning("HEADLESS_CONFIG gesetzt, Datei fehlt aber: %s", path)
        return
    async with async_session() as session:
        device_count = await session.scalar(select(func.count(Device.id)))
        if device_count:
            log.info("Headless-Config übersprungen: Geräte bereits konfiguriert")
            return
    from .api.system import ConfigImportError, apply_config_import  # zirkulären Import vermeiden

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        await apply_config_import(data, create_admin_if_missing=True)
    except (ValueError, ConfigImportError) as exc:
        # Nicht abstürzen: Die Anlage bleibt erreichbar und lässt sich über
        # die Oberfläche einrichten; die Ursache steht im Protokoll.
        log.error("Headless-Konfiguration %s nicht importiert: %s", path, exc)
        return
    log.info("Headless-Konfiguration importiert: %s", path)


async def setup_demo_mode() -> None:
    """Demo-Modus: legt simulierte Geräte + Demo-Admin an (falls leer)."""
    async with async_session() as session:
        device_count = await session.scalar(select(func.count(Device.id)))
        if device_count:
            return
        demo_devices = [
            Device(name="PV-Anlage (Simulation)", category="inverter", driver_id="sim_inverter",
                   config={"kwp": 9.8}, settings={}),
            Device(name="Netzzähler (Simulation)", category="meter", driver_id="sim_meter", config={}, settings={}),
            Device(name="Wallbox (Simulation)", category="wallbox", driver_id="sim_wallbox",
                   config={"max_current": 16, "car_capacity_kwh": 62},
                   settings={"mode": "pv_only", "min_current": 6, "max_current": 16, "phases_mode": "auto"}),
            Device(name="Heizstab (Simulation)", category="water_heater", driver_id="sim_water_heater",
                   config={"rated_power": 3000}, settings={"mode": "pv_only", "max_power_w": 3000}),
            Device(name="Hausbatterie (Simulation)", category="battery", driver_id="sim_battery",
                   config={"capacity_kwh": 9.6, "max_power": 5000, "allow_active_control": True},
                   settings={"manage_reserve": True}),
        ]
        session.add_all(demo_devices)
        await session.flush()
        prio = await session.get(Setting, "priority")
        if prio is not None:
            # Standard-Kette: Auto → Warmwasser (der Speicher hat seinen Platz
            # über 'Batterie zuerst bis …")
            ids = {d.driver_id: d.id for d in demo_devices}
            prio.value = {"order": [ids["sim_wallbox"], ids["sim_water_heater"]]}
        user_count = await session.scalar(select(func.count(User.id)))
        if not user_count:
            session.add(User(email="demo@minepower.de", name="Demo",
                             password_hash=hash_password("demo1234"), role="admin"))
            log.warning("Demo-Admin angelegt: demo@minepower.de / demo1234 – bitte ändern!")
        await session.commit()
        log.info("Demo-Modus: simulierte Anlage angelegt")
