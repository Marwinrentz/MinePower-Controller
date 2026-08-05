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
        "interval_s": 3,
        "grid_target_w": 0,
        "deadband_w": 100,
        "smoothing_samples": 3,
        "house_limit_a": 35,
        # Hausspeicher zuerst: Weder Lade- noch Entladeleistung gilt als
        # Überschuss für Auto und Warmwasser. Die beiden SoC-Schwellen
        # darunter greifen nur, wenn das bewusst abgeschaltet wird.
        "battery_priority": True,
        "battery_reserve_soc": 20,
        "ev_lock_soc": 30,
        # Ab wie viel Entladeleistung eine laufende Last hart abschaltet.
        # Darunter wird nur abgeregelt – sonst beendet jede kleine Lastspitze
        # im Haus die Ladung und der Regler taktet sich selbst aus.
        "battery_drain_limit_w": 250,
        # Weniger verschenken (siehe core/regulation.py)
        "adaptive_deadband": True,
        "deadband_min_w": 40,
        "deadband_max_w": 400,
        "gap_fill_min_w": 25,
        "use_forecast": True,
    },
    "priority": {"order": []},
    "tariff": {"provider": "none", "cheap_limit_ct": 15},
    "forecast": {"provider": "none"},
    "notifications": {"ntfy_url": "", "telegram_token": "", "telegram_chat_id": "", "email": ""},
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


async def init_db() -> None:
    """Schema anlegen (idempotent) + Timescale-Hypertable & Retention."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            await conn.execute(
                text("SELECT create_hypertable('measurements', 'time', if_not_exists => TRUE, migrate_data => TRUE)")
            )
            # Rohdaten 90 Tage aufbewahren (Downsampling siehe docs/architecture.md)
            await conn.execute(
                text("SELECT add_retention_policy('measurements', INTERVAL '90 days', if_not_exists => TRUE)")
            )
            log.info("TimescaleDB-Hypertable aktiv")
        except Exception as exc:  # noqa: BLE001
            # Läuft auch auf reinem PostgreSQL (ohne Timescale) – nur langsamer.
            log.warning("TimescaleDB nicht verfügbar, nutze reines PostgreSQL: %s", exc)


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
    data = json.loads(path.read_text(encoding="utf-8"))
    from .api.system import apply_config_import  # zirkulären Import vermeiden

    await apply_config_import(data, create_admin_if_missing=True)
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
                   config={"capacity_kwh": 9.6, "max_power": 5000}, settings={"reserve_soc": 20}),
        ]
        session.add_all(demo_devices)
        await session.flush()
        prio = await session.get(Setting, "priority")
        if prio is not None:
            # Standard-Kette: Batterie → Auto → Warmwasser
            ids = {d.driver_id: d.id for d in demo_devices}
            prio.value = {"order": [ids["sim_battery"], ids["sim_wallbox"], ids["sim_water_heater"]]}
        user_count = await session.scalar(select(func.count(User.id)))
        if not user_count:
            session.add(User(email="demo@minepower.de", name="Demo",
                             password_hash=hash_password("demo1234"), role="admin"))
            log.warning("Demo-Admin angelegt: demo@minepower.de / demo1234 – bitte ändern!")
        await session.commit()
        log.info("Demo-Modus: simulierte Anlage angelegt")
