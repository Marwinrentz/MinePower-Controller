"""Geräte-CRUD, Verbindungstest mit Live-Readback, manuelle Overrides."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import runtime
from ..core.regulation import WallboxController, WaterHeaterController
from ..db import get_db
from ..drivers import registry
from ..drivers.base import BatteryDriver, BatteryMode, TestResult
from ..models import Device, Setting, User
from ..schemas import (
    DeviceActionRequest,
    DeviceCreate,
    DeviceOut,
    DeviceUpdate,
    TestConnectionRequest,
)
from ..security import get_current_user, require_role
from ..services.audit import log_event

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=list[DeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    devices = (await db.scalars(select(Device).order_by(Device.id))).all()
    # Laufzeitstatus aus dem Loop mischen
    try:
        loop = runtime.get_loop()
        for d in devices:
            managed = loop.devices.get(d.id)
            if managed:
                d.status = "online" if managed.online else ("error" if managed.last_error else "offline")
                d.last_error = managed.last_error
                d.last_seen = managed.last_seen
    except RuntimeError:
        pass
    return devices


@router.post("", response_model=DeviceOut)
async def create_device(body: DeviceCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    try:
        meta = registry.get_driver_class(body.driver_id).meta
    except KeyError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unbekannter Treiber: {body.driver_id}")
    if meta.category.value != body.category:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Kategorie passt nicht zum Treiber")
    device = Device(**body.model_dump())
    db.add(device)
    await db.commit()
    await db.refresh(device)
    await log_event(f"Gerät angelegt: {device.name} ({body.driver_id})", category="config", user_id=user.id)
    _reload()
    return device


@router.patch("/{device_id}", response_model=DeviceOut)
async def update_device(device_id: int, body: DeviceUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gerät nicht gefunden")
    for field in ("name", "config", "settings", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(device, field, value)
    await db.commit()
    await db.refresh(device)
    await log_event(f"Gerät geändert: {device.name}", category="config", user_id=user.id)
    _reload()
    return device


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    device = await db.get(Device, device_id)
    if device:
        name = device.name
        await db.delete(device)
        # Verwaiste ID aus der Prioritätskette entfernen – sonst bleibt sie
        # dort für immer stehen und ein später neu angelegtes Gerät landet
        # unbemerkt am Ende der Kette statt an der gewünschten Stelle.
        priority = await db.get(Setting, "priority")
        if priority is not None:
            order = [i for i in (priority.value or {}).get("order", []) if i != device_id]
            priority.value = {"order": order}
        await db.commit()
        await log_event(f"Gerät gelöscht: {name}", category="config", user_id=user.id)
        _reload()


@router.post("/test", response_model=TestResult)
async def test_connection(body: TestConnectionRequest, _: User = Depends(require_role("admin"))):
    """Verbindungstest für den Setup-Assistenten – Gerät muss nicht gespeichert sein."""
    try:
        driver = registry.create_driver(body.driver_id, body.config)
    except KeyError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unbekannter Treiber: {body.driver_id}")
    # Der Schreibtest wartet bewusst auf die Übernahme im Gerät (Fahrzeuge
    # brauchen dafür ein paar Sekunden) – deshalb ein größeres Zeitfenster.
    timeout = 40 if body.include_write else 20
    try:
        return await asyncio.wait_for(
            driver.test_connection(include_write=body.include_write), timeout=timeout
        )
    except asyncio.TimeoutError:
        return TestResult(
            ok=False,
            message="Zeitüberschreitung – das Gerät antwortet nicht. IP-Adresse, Port und "
                    "Unit-ID prüfen; bei Modbus-Geräten außerdem, ob Modbus TCP im Gerät "
                    "aktiviert ist.",
        )
    finally:
        try:
            await driver.disconnect()
        except Exception:  # noqa: BLE001 – Aufräumen darf den Test nicht kippen
            pass


@router.post("/{device_id}/action")
async def device_action(
    device_id: int,
    body: DeviceActionRequest,
    user: User = Depends(require_role("user")),
):
    """Manueller Override – jederzeit, auch bei aktiver Automatik."""
    loop = runtime.get_loop()
    dev = loop.devices.get(device_id)
    if dev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gerät nicht im Regelkreis")

    action = body.action
    if isinstance(dev.controller, WallboxController):
        if action == "fast":
            dev.controller.override = "fast"
        elif action == "stop":
            dev.controller.override = "stop"
        elif action == "auto":
            dev.controller.override = None
        elif action == "set_mode":
            dev.controller.s.mode = str(body.value)
            await _persist_mode(device_id, str(body.value))
        elif action == "set_charge_limit":
            await _set_charge_limit(dev, device_id, body.value)
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unbekannte Aktion: {action}")
    elif isinstance(dev.controller, WaterHeaterController):
        if action == "boost":
            dev.controller.boost = True
        elif action == "boost_off":
            dev.controller.boost = False
        elif action == "set_mode":
            dev.controller.s.mode = str(body.value)
            await _persist_mode(device_id, str(body.value))
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unbekannte Aktion: {action}")
    elif isinstance(dev.driver, BatteryDriver) and action == "battery_mode":
        try:
            mode = BatteryMode(str(body.value))
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Ungültiger Batteriemodus: {body.value}")
        try:
            await dev.driver.set_mode(mode)
        except NotImplementedError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Treiber unterstützt keine Batteriesteuerung")
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Aktion {action} für dieses Gerät nicht möglich")

    await log_event(f"Override: {action} → {dev.name}", category="control", user_id=user.id,
                    data={"value": body.value})
    return {"ok": True}


async def _set_charge_limit(dev, device_id: int, value) -> None:
    """Ladegrenze im Fahrzeug setzen (z. B. 80 % statt 100 %).

    Dauerhaft auf 100 % zu laden verkürzt die Lebensdauer von Lithium-Akkus
    spürbar. Der Wert wird im Fahrzeug selbst gesetzt – MinePower merkt ihn
    sich zusätzlich, damit die Oberfläche ihn auch dann kennt, wenn das
    Fahrzeug gerade schläft und keine Daten liefert."""
    try:
        limit = int(float(value))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Ungültige Ladegrenze: {value}")
    if not 50 <= limit <= 100:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Ladegrenze muss zwischen 50 % und 100 % liegen.",
        )
    try:
        await dev.driver.set_charge_limit(limit)
    except NotImplementedError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Dieses Gerät unterstützt keine Ladegrenze. Sie lässt sich nur direkt am "
            "Fahrzeug setzen – eine Wallbox kennt den Ladestand des Autos nicht.",
        )
    except Exception as exc:  # noqa: BLE001 – Klartext ins GUI statt 500er
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Fahrzeug hat die Ladegrenze abgelehnt: {exc}")

    from ..db import async_session

    async with async_session() as session:
        device = await session.get(Device, device_id)
        if device:
            device.settings = {**(device.settings or {}), "charge_limit_soc": limit}
            await session.commit()


async def _persist_mode(device_id: int, mode: str) -> None:
    from ..db import async_session

    async with async_session() as session:
        device = await session.get(Device, device_id)
        if device:
            device.settings = {**(device.settings or {}), "mode": mode}
            await session.commit()


def _reload() -> None:
    try:
        runtime.get_loop().request_reload()
    except RuntimeError:
        pass
