"""Ersteinrichtung ohne Bezug auf eine bestimmte Anlage.

Jede Kombination wird über die API angelegt (inkl. Feldprüfung) und einmal
durch den Regelkreis geschickt: leere DB, nur PV, nur Zähler, PV + Zähler,
mit/ohne Batterie, Auto, Warmwasser, andere Marken. Kein Fall darf
abstürzen; ohne Netzmessung muss die Regelung sicher pausieren.
"""
import re
from pathlib import Path

import pytest

from app.api.devices import create_device
from app.api.system import has_grid_measurement
from app.core.loop import ControlLoop
from app.db import async_session
from app.drivers import registry
from app.drivers.base import ConfigField, DriverMeta
from app.models import Device
from app.schemas import DeviceCreate, DeviceOut, UserOut

registry.discover()

SIM = {
    "pv": ("inverter", "sim_inverter", {"kwp": 6}),
    "meter": ("meter", "sim_meter", {}),
    "battery": ("battery", "sim_battery", {"capacity_kwh": 8, "max_power": 4000}),
    "car": ("wallbox", "sim_wallbox", {"max_current": 16, "car_capacity_kwh": 50}),
    "water": ("water_heater", "sim_water_heater", {"rated_power": 2000}),
}

COMBOS = {
    "leer": [],
    "nur_pv": ["pv"],
    "nur_zaehler": ["meter"],
    "pv_zaehler": ["pv", "meter"],
    "pv_batterie": ["pv", "meter", "battery"],
    "ohne_auto": ["pv", "meter", "battery", "water"],
    "ohne_warmwasser": ["pv", "meter", "battery", "car"],
    "alles": ["pv", "meter", "battery", "car", "water"],
}


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.db import Base, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


class _Admin:
    id = None
    role = "admin"


async def _add(category, driver, config, name=None):
    async with async_session() as db:
        await create_device(DeviceCreate(name=name or driver, category=category, driver_id=driver, config=config),
                            db=db, user=_Admin())


async def _run() -> ControlLoop:
    loop = ControlLoop()
    loop.tariff = None
    await loop._reload()
    for dev in loop.devices.values():
        try:
            await dev.driver.connect()
        except Exception:  # noqa: BLE001 – unerreichbare Geräte sind hier gewollt
            pass
    await loop._step()
    return loop


@pytest.mark.parametrize("combo", list(COMBOS))
async def test_setup_combination_runs(combo):
    for key in COMBOS[combo]:
        await _add(*SIM[key])
    loop = await _run()
    snap = loop.snapshot
    assert snap is not None
    for key in ("pv_power", "grid_power", "house_power", "wallbox_power", "water_power", "devices"):
        assert key in snap
    async with async_session() as db:
        rows = (await db.execute(Device.__table__.select())).all()
    grid = has_grid_measurement(rows)
    assert grid == ("meter" in COMBOS[combo])
    if not grid:
        assert snap["safety"], "ohne Netzmessung muss die Regelung pausieren"
    if "battery" not in COMBOS[combo]:
        assert snap["battery"] is None


async def test_other_brands_without_hardware_do_not_crash():
    """Andere Marken, Gerät nicht erreichbar: offline, sichere Pause, kein Absturz."""
    await _add("inverter", "ha_inverter", {"url": "http://192.0.2.5:8123", "token": "t", "pv_entity": "sensor.pv"})
    await _add("meter", "http_meter", {"url": "http://192.0.2.10/status", "power_path": "p"})
    await _add("wallbox", "keba_p30", {"host": "192.0.2.15", "local_port": 0})
    await _add("water_heater", "tasmota_relay_heater", {"host": "192.0.2.12"})
    loop = await _run()
    assert loop.snapshot["safety"]


# ---------------------------------------------------------------- Schema ↔ Frontend

TYPES = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "types.ts").read_text(encoding="utf-8")


def ts_keys(name: str) -> set[str]:
    match = re.search(rf"export type {name} = \{{(.*?)\n\}};", TYPES, re.S)
    assert match, name
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", match.group(1), flags=re.S)
    return set(re.findall(r"^\s*(\w+)\??:", body, re.M))


@pytest.mark.parametrize("ts_name,model", [
    ("ConfigField", ConfigField), ("DriverMeta", DriverMeta), ("Device", DeviceOut), ("User", UserOut),
])
def test_frontend_types_match_api_schemas(ts_name, model):
    assert ts_keys(ts_name) == set(model.model_fields), ts_name
