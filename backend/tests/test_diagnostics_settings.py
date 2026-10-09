"""Diagnose-Befunde, Duplikat-Erkennung und Absicherung der Einstellungs-API."""
from sqlalchemy import select

from app.api.settings_api import get_all_settings, update_settings
from app.core.loop import ControlLoop
from app.db import async_session
from app.drivers.simulation import SimulationWorld
from app.models import Device, Event, Setting
from app.schemas import SettingsUpdate
from app.services.diagnostics import findings, mask_vin

from .test_price_window_integration import build_loop


class _User:
    def __init__(self, role="admin"):
        self.id = None
        self.role = role


# ------------------------------------------------------------ Einstellungs-API


async def _seed_system_secret():
    async with async_session() as session:
        row = await session.get(Setting, "system")
        if row is None:
            session.add(Setting(key="system", value={"jwt_secret": "geheim"}))
        else:
            row.value = {"jwt_secret": "geheim"}
        await session.commit()


async def test_settings_never_expose_jwt_secret():
    await _seed_system_secret()
    async with async_session() as db:
        out = await get_all_settings(db=db, user=_User("user"))
    assert "system" not in out.values
    assert "geheim" not in str(out.values)


async def test_settings_put_ignores_system_section():
    await _seed_system_secret()
    async with async_session() as db:
        await update_settings(
            SettingsUpdate(values={"system": {"jwt_secret": "übernommen"}}), db=db, user=_User()
        )
    async with async_session() as db:
        row = await db.get(Setting, "system")
    assert row.value["jwt_secret"] == "geheim"


async def test_settings_log_only_changed_sections():
    async with async_session() as db:
        await update_settings(SettingsUpdate(values={"ui": {"default_language": "de"}}), db=db, user=_User())
        await update_settings(
            SettingsUpdate(values={"ui": {"default_language": "de"}, "forecast": {"provider": "x"}}),
            db=db, user=_User(),
        )
    async with async_session() as db:
        last = (await db.scalars(select(Event).order_by(Event.id.desc()))).first()
    assert last.message == "Einstellungen geändert: forecast"


async def test_tokens_redacted_for_non_admins():
    async with async_session() as db:
        await update_settings(
            SettingsUpdate(values={"tariff": {"provider": "tibber", "tibber_token": "abc"}}), db=db, user=_User()
        )
        viewer = await get_all_settings(db=db, user=_User("user"))
        admin = await get_all_settings(db=db, user=_User("admin"))
    # Seit 3.0: auch Admins sehen Zugangsdaten nur maskiert, gespeichert
    # wird verschlüsselt, '•••" beim Speichern behält den Wert.
    assert viewer.values["tariff"]["tibber_token"] == "•••"
    assert admin.values["tariff"]["tibber_token"] == "•••"
    async with async_session() as db:
        stored = (await db.get(Setting, "tariff")).value["tibber_token"]
        assert stored.startswith("enc:v1:") and "abc" not in stored
        await update_settings(
            SettingsUpdate(values={"tariff": {"provider": "tibber", "tibber_token": "•••"}}), db=db, user=_User()
        )
        kept = (await db.get(Setting, "tariff")).value["tibber_token"]
    from app.core.secrets import decrypt
    assert decrypt(kept) == "abc"


# ------------------------------------------------------------ Duplikate


async def test_duplicate_inverter_counts_pv_only_once(monkeypatch):
    monkeypatch.setattr(SimulationWorld, "pv_power", lambda self: 4000.0)
    loop = await build_loop(price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only")
    async with async_session() as session:
        session.add(Device(name="PV (doppelt)", category="inverter", driver_id="sim_inverter",
                           config={"kwp": 9.8}, settings={}))
        await session.commit()
    await loop._reload()
    for dev in loop.devices.values():
        await dev.driver.connect()

    await loop._step()

    assert loop.snapshot["pv_power"] < 4500  # nicht 8000
    assert loop.duplicate_devices == ["PV (doppelt)"]
    assert any("Doppelt angelegt" in w for w in loop.snapshot["warnings"])


# ------------------------------------------------------------ Befunde


class _Row:
    def __init__(self, id, name, driver_id, config):
        self.id, self.name, self.driver_id, self.config = id, name, driver_id, config


def test_finding_for_duplicate_devices():
    loop = ControlLoop()
    rows = [
        _Row(1, "Sungrow SH", "sungrow_sh", {"host": "192.0.2.140", "port": 502, "unit_id": 1}),
        _Row(2, "Sungrow SH", "sungrow_sh", {"host": "192.0.2.140", "port": 502, "unit_id": 1}),
        _Row(3, "Zähler", "sungrow_meter", {"host": "192.0.2.140", "port": 502, "unit_id": 1}),
    ]
    out = findings(loop, rows, {})
    dup = [f for f in out if f["title"] == "Gerät doppelt angelegt"]
    assert len(dup) == 1 and "ID 2" in dup[0]["detail"]


async def test_finding_grid_charge_without_write_permission():
    """Genau die Konstellation aus dem Diagnosebericht: Netzladen an,
    Batteriegerät ohne Schreibfreigabe → es wird nie ein Befehl gesendet."""
    loop = await build_loop(
        price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only",
        battery_release_soc=80.0, battery_grid_charge_enabled=True,
    )
    out = findings(loop, list(loop.devices.values()), {})
    assert any(f["title"] == "Netzladen an, Batterie nicht steuerbar" for f in out)


def test_vin_is_masked():
    assert mask_vin("XP7TEST0000000001") == "XP7…0001"


# ------------------------------------------------------------ Verhalten (Ereignisse)

from datetime import datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def ev(message, hours_ago, category="control", data=None):
    t = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc) - timedelta(hours=hours_ago)
    return SimpleNamespace(time=t, message=message, category=category, data=data or {}, level="warning")


def _elwa_row():
    return _Row(5, "AC ELWA 2", "mypv_acthor", {"host": "192.0.2.2"})


def test_recurring_heater_disobedience_points_to_device_program():
    """Diagnosebericht 03.10.: täglich 04:00 UTC = 06:00 Ortszeit, dazu
    nachmittags – die Auswertung meldete davon nichts."""
    loop = ControlLoop()
    events = [ev("AC ELWA 2 folgt der Regelung nicht: höchstens 0 W, gemessen 2966 W.", h)
              for h in (6, 30, 54, 78)]
    out = findings(loop, [_elwa_row()], {}, events=events)
    f = next(x for x in out if "AC ELWA 2" in x["title"])
    # Kurz: Titel sagt was, Detail wann, Fix wo – die lange Erklärung steht
    # in der Geräteeinrichtung.
    assert "Geräteprogramm" in f["title"]
    assert "06 Uhr" in f["detail"]
    assert "Warmwasser-Sicherstellung" in f["fix"] and "Webinterface" in f["fix"]
    assert f["count"] == 4
    assert len(f["detail"]) < 120


def test_vehicle_disobedience_explains_self_start():
    loop = ControlLoop()
    row = _Row(3, "Fahrzeug 1", "tesla_vehicle", {"vin": "XP7TEST0000000001"})
    row.category = "wallbox"
    events = [ev("Fahrzeug 1 folgt der Regelung nicht: aus, gemessen 4248 W.", h) for h in (2, 20)]
    out = findings(loop, [row], {}, events=events)
    f = next(x for x in out if "Fahrzeug 1" in x["title"])
    assert "Anstecken" in f["detail"]


def test_proxy_outage_is_reported_with_address():
    loop = ControlLoop()
    msg = ("Gerät offline: Fahrzeug 1 (ConnectionError: Tesla-Endpunkt http://192.0.2.33:8080 "
           "nicht erreichbar – läuft der TeslaBleHttpProxy und stimmt der Port?)")
    row = _Row(3, "Fahrzeug 1", "tesla_vehicle", {"vin": "XP7TEST0000000001"})
    row.category = "wallbox"
    out = findings(loop, [row], {}, events=[ev(msg, 3, category="device")])
    f = next(x for x in out if "offline" in x["title"])
    assert "192.0.2.33:8080" in f["detail"]
    # Abhilfe kommt aus den Treiber-Metadaten, nicht aus Markennamen im Kern
    assert "Gateway" in f["fix"]


def test_waste_finding_names_reasons_and_fix():
    loop = ControlLoop()
    events = [ev("Überschuss ins Netz: 3.0 kWh in 60 min", 5, category="waste",
                 data={"kwh": 3.0, "reasons": ["AC ELWA 2: Zieltemperatur 60 °C erreicht", "Tesla: nicht erreichbar"]})]
    out = findings(loop, [], {}, events=events, summary={"wasted_kwh": 6.9, "wasted_pct": 11.0})
    f = next(x for x in out if "verschenkt" in x["title"])
    assert f["level"] == "warn"
    assert "Zieltemperatur" in f["detail"]
    assert "Mit Überschuss heizen bis" in f["fix"]


def test_tariff_zero_surcharges_ok_for_tibber_but_warned_for_awattar():
    class T:
        provider = "tibber"
        cheap_limit_ct = 24.0

        def status(self):
            return {"has_current_price": True}

    loop = ControlLoop()
    loop.tariff = T()
    loop.cfg.battery_grid_charge_enabled = True
    zero = {"tariff": {"grid_fees_ct": 0, "levies_ct": 0, "supplier_margin_ct": 0, "vat_pct": 0}}
    out = findings(loop, [], zero)
    assert any(f["level"] == "ok" and "Tibber" in f["detail"] for f in out)

    T.provider = "awattar"
    out = findings(loop, [], zero)
    assert any(f["level"] == "warn" and "Aufschläge" in f["title"] for f in out)


def test_reserve_finding_when_battery_dropped_below_reserve():
    loop = ControlLoop()
    bat = _Row(4, "Sungrow Batterie", "sungrow_battery", {"host": "192.0.2.140"})
    bat.category, bat.settings = "battery", {"manage_reserve": False}
    out = findings(loop, [bat], {}, history={"battery_soc": [{"value": 40}, {"value": 5.0}]})
    f = next(x for x in out if "Reserve" in x["title"])
    assert "Batterie aktiv steuern" in f["fix"]


def test_findings_are_sorted_by_severity():
    loop = ControlLoop()
    loop.cfg.interval_s = 20
    events = [ev("AC ELWA 2 folgt der Regelung nicht: höchstens 0 W, gemessen 2966 W.", h) for h in (6, 30, 54)]
    out = findings(loop, [_elwa_row()], {}, events=events)
    levels = [f["level"] for f in out]
    assert levels == sorted(levels, key=lambda lv: {"error": 0, "warn": 1, "info": 2, "ok": 3}[lv])


async def test_findings_endpoint_survives_failing_statistics(monkeypatch):
    """Scheitert die Kennzahl-Abfrage (hier: SQLite kennt die PostgreSQL-
    Funktionen nicht), gibt es trotzdem Befunde. Früher ließ der Rollback die
    schon geladenen Ereignisse ablaufen, und der Zugriff darauf endete in
    einem 500 auf der Diagnose-Seite."""
    from app.api.system import findings_endpoint
    from app.core import runtime
    from app.services.audit import log_event

    loop = await build_loop(price_ct=30.0, battery_controllable=False, wallbox_mode="pv_only")
    monkeypatch.setattr(runtime, "get_loop", lambda: loop)
    await log_event("Wallbox (Simulation) folgt der Regelung nicht", level="warning", category="device")
    async with async_session() as db:
        out = await findings_endpoint(range="7d", db=db, user=_User())
    assert isinstance(out["findings"], list)
