"""Mehrere Geräte je Klasse: Limits, Migration, Summierung."""
import pytest
from fastapi import HTTPException

from app.api.devices import create_device, update_device
from app.core.limits import enforce_on_startup
from app.core.loop import combine_inverter_batteries
from app.db import async_session
from app.drivers.base import InverterData
from app.models import Device
from app.schemas import DeviceCreate, DeviceUpdate


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.db import Base, engine
    from app.drivers import registry
    registry.discover()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


class _Admin:
    id = None
    role = "admin"


async def _create(category, driver, name, **config):
    async with async_session() as db:
        return await create_device(DeviceCreate(name=name, category=category, driver_id=driver, config=config),
                                   db=db, user=_Admin())


@pytest.mark.parametrize("category,driver", [("meter", "sim_meter"), ("battery", "sim_battery")])
async def test_second_meter_or_battery_is_rejected(category, driver):
    first = await _create(category, driver, "A")
    with pytest.raises(HTTPException) as exc:
        await _create(category, driver, "B")
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "category_limit"
    # deaktiviert anlegen geht, aktivieren nicht
    async with async_session() as db:
        off = await create_device(DeviceCreate(name="C", category=category, driver_id=driver, config={}, enabled=False),
                                  db=db, user=_Admin())
        with pytest.raises(HTTPException) as exc:
            await update_device(off.id, DeviceUpdate(enabled=True), db=db, user=_Admin())
        assert exc.value.detail["code"] == "category_limit"
        await update_device(first.id, DeviceUpdate(enabled=False), db=db, user=_Admin())
        await update_device(off.id, DeviceUpdate(enabled=True), db=db, user=_Admin())


@pytest.mark.parametrize("category,driver", [("wallbox", "sim_wallbox"), ("water_heater", "sim_water_heater"),
                                             ("inverter", "sim_inverter")])
async def test_unlimited_classes_accept_several(category, driver):
    await _create(category, driver, "A")
    await _create(category, driver, "B")


async def test_startup_disables_surplus_meters_and_batteries():
    async with async_session() as db:
        for i in range(3):
            db.add(Device(name=f"Zähler {i}", category="meter", driver_id="sim_meter", config={}, settings={}))
        db.add(Device(name="Speicher 1", category="battery", driver_id="sim_battery", config={}, settings={}))
        db.add(Device(name="Speicher 2", category="battery", driver_id="sim_battery", config={}, settings={}))
        await db.commit()
    disabled = await enforce_on_startup()
    assert disabled == ["Zähler 1", "Zähler 2", "Speicher 2"]


async def test_invalid_config_is_rejected_with_field():
    with pytest.raises(HTTPException) as exc:
        await _create("meter", "shelly_3em", "Shelly", host="", generation="2")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "invalid_config"
    assert exc.value.detail["fields"][0]["key"] == "host"
    with pytest.raises(HTTPException):
        await _create("inverter", "sunspec_inverter", "SunSpec", host="192.0.2.5", port="abc", unit_id=1)


def test_two_hybrid_inverters_batteries_are_combined():
    out = combine_inverter_batteries([
        InverterData(pv_power=1, battery_soc=80, battery_power=1000, battery_capacity_kwh=10),
        InverterData(pv_power=1, battery_soc=20, battery_power=-400, battery_capacity_kwh=5),
    ])
    assert out.power == 600
    assert out.soc == pytest.approx(60.0)
    assert out.capacity_kwh == 15
