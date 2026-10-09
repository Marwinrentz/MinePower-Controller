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

from ..core.limits import CATEGORY_LIMITS, active_count, limit_error
from ..core.secrets import mask_config, protect_config, reveal_config
from ..drivers.config_validation import config_error, validate_config

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
    return [_out(d) for d in devices]


def _out(device: Device) -> DeviceOut:
    """Antwort mit maskierten Zugangsdaten (nie im Klartext ausliefern)."""
    out = DeviceOut.model_validate(device)
    return out.model_copy(update={"config": mask_config(device.driver_id, device.config or {})})


@router.get("/limits")
async def category_limits(_: User = Depends(get_current_user)) -> dict[str, int]:
    """Höchstzahl aktiver Geräte je Klasse (fehlende Klasse = unbegrenzt)."""
    return CATEGORY_LIMITS


@router.post("", response_model=DeviceOut)
async def create_device(body: DeviceCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    try:
        meta = registry.get_driver_class(body.driver_id).meta
    except KeyError:
        raise HTTPException(422, f"Unbekannter Treiber: {body.driver_id}")
    if meta.category.value != body.category:
        raise HTTPException(422, "Kategorie passt nicht zum Treiber")
    if body.enabled and body.category in CATEGORY_LIMITS \
            and await active_count(db, body.category) >= CATEGORY_LIMITS[body.category]:
        raise HTTPException(status.HTTP_409_CONFLICT, limit_error(body.category))
    errors = validate_config(meta, body.config or {})
    if errors:
        raise HTTPException(422, config_error(errors))
    duplicate = await _find_duplicate(db, body.driver_id, body.config or {})
    if duplicate is not None:
        # Im Diagnosebericht stand derselbe Sungrow SH zweimal (angelegt im
        # Abstand von 30 s) – die PV-Leistung zählte doppelt. Dieselbe
        # Adresse mit demselben Treiber ist dasselbe Gerät.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Dieses Gerät ist bereits angelegt: '{duplicate.name}' (gleicher Treiber, gleiche "
            f"Adresse). Zum Ändern das bestehende Gerät bearbeiten.",
        )
    data = body.model_dump()
    data["config"] = protect_config(body.driver_id, data.get("config") or {})
    device = Device(**data)
    db.add(device)
    await db.commit()
    await db.refresh(device)
    await log_event(f"Gerät angelegt: {device.name} ({body.driver_id})", category="config", user_id=user.id)
    _reload()
    return _out(device)


@router.patch("/{device_id}", response_model=DeviceOut)
async def update_device(device_id: int, body: DeviceUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gerät nicht gefunden")
    if body.settings is not None and device.category == "water_heater":
        _validate_boost(body.settings)
    if body.settings is not None:
        _validate_common(body.settings)
    if body.enabled and not device.enabled and device.category in CATEGORY_LIMITS \
            and await active_count(db, device.category, exclude_id=device.id) >= CATEGORY_LIMITS[device.category]:
        raise HTTPException(status.HTTP_409_CONFLICT, limit_error(device.category))
    if body.config is not None:
        meta = registry.get_driver_class(device.driver_id).meta
        errors = validate_config(meta, protect_config(device.driver_id, body.config, device.config or {}),
                                 masked_ok=True)
        if errors:
            raise HTTPException(422, config_error(errors))
        body.config = protect_config(device.driver_id, body.config, device.config or {})
    for field in ("name", "config", "settings", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(device, field, value)
    await db.commit()
    await db.refresh(device)
    await log_event(f"Gerät geändert: {device.name}", category="config", user_id=user.id)
    _reload()
    return _out(device)


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
async def test_connection(body: TestConnectionRequest, db: AsyncSession = Depends(get_db),
                          _: User = Depends(require_role("admin"))):
    """Verbindungstest für den Setup-Assistenten – Gerät muss nicht gespeichert sein."""
    config = dict(body.config or {})
    if body.device_id is not None:
        # Bestehendes Gerät: '•••" heißt 'gespeicherten Wert verwenden".
        stored = await db.get(Device, body.device_id)
        if stored is not None and stored.driver_id == body.driver_id:
            config = protect_config(body.driver_id, config, stored.config or {})
    config = reveal_config(body.driver_id, config)
    try:
        meta = registry.get_driver_class(body.driver_id).meta
    except KeyError:
        raise HTTPException(422, f"Unbekannter Treiber: {body.driver_id}")
    errors = validate_config(meta, config)
    if errors:
        return TestResult(ok=False, message="Eingabe ungültig: " + "; ".join(f"{e['label']}: {e['error']}" for e in errors))
    try:
        driver = registry.create_driver(body.driver_id, config)
    except KeyError:
        raise HTTPException(422, f"Unbekannter Treiber: {body.driver_id}")
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
        # Jeder Eingriff endet: 'Sofort" am Ladeende/Abstecken, 'Aus" beim
        # Abstecken – und beide spätestens nach der eingestellten Zeit.
        if action == "fast":
            dev.controller.set_override("fast", hours=loop.cfg.override_fast_h)
        elif action == "stop":
            dev.controller.set_override("stop", hours=loop.cfg.override_stop_h)
        elif action == "auto":
            dev.controller.set_override(None)
        elif action == "set_mode":
            dev.controller.s.mode = str(body.value)
            await _persist_mode(device_id, str(body.value))
            dev.settings["mode"] = str(body.value)
        elif action == "set_charge_limit":
            await _set_charge_limit(dev, device_id, body.value)
        elif action == "wake":
            # Ein schlafendes Fahrzeug antwortet über Funk nicht und fällt
            # damit aus der Regelung. Der Treiber konnte es längst wecken —
            # es gab nur keinen Weg, das auszulösen, außer zu warten.
            wake = getattr(dev.driver, "wake_up", None)
            if wake is None:
                raise HTTPException(422,
                                    "Dieser Ladepunkt kann kein Fahrzeug wecken")
            try:
                await wake()
            except Exception as exc:  # noqa: BLE001 – Grund gehört ins GUI
                raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                    f"Wecken fehlgeschlagen: {exc}") from exc
        else:
            raise HTTPException(422, f"Unbekannte Aktion: {action}")
    elif isinstance(dev.controller, WaterHeaterController):
        if action == "boost":
            dev.controller.boost = True
            await loop.save_runtime()
        elif action == "boost_off":
            dev.controller.boost = False
            await loop.save_runtime()
        elif action == "set_mode":
            dev.controller.s.mode = str(body.value)
            await _persist_mode(device_id, str(body.value))
            # Auch im Speicher des Loops nachziehen: Sonst unterscheidet sich
            # der Fingerabdruck beim nächsten Reload, und das Gerät würde neu
            # aufgebaut (Verbindung und Reglerzustand weg).
            dev.settings["mode"] = str(body.value)
        else:
            raise HTTPException(422, f"Unbekannte Aktion: {action}")
    elif isinstance(dev.driver, BatteryDriver) and action == "battery_mode":
        # Früher schrieb dieser Zweig direkt auf den Wechselrichter – parallel
        # zum Loop, der im nächsten Takt womöglich etwas anderes schrieb.
        # Jetzt läuft auch er über den manuellen Sollzustand im Loop
        # (siehe api/battery.py): ein Schreiber, definierte Reihenfolge.
        from .battery import BatteryCommand, battery_manual

        try:
            mode = BatteryMode(str(body.value))
        except ValueError:
            raise HTTPException(422, f"Ungültiger Batteriemodus: {body.value}")
        mapping = {BatteryMode.AUTO: "auto", BatteryMode.FORCE_CHARGE: "charge",
                   BatteryMode.FORCE_DISCHARGE: "discharge"}
        if mode not in mapping:
            raise HTTPException(422,
                                "Sperren ist kein manueller Befehl – nur Laden, Entladen oder Automatik.")
        return await battery_manual(BatteryCommand(action=mapping[mode], device_id=device_id), user)
    else:
        raise HTTPException(422, f"Aktion {action} für dieses Gerät nicht möglich")

    # Sofort einen Takt auslösen, statt bis zu `interval_s` zu warten.
    loop.kick()
    await log_event(f"Override: {action} → {dev.name}", category="control", user_id=user.id,
                    data={"value": body.value})
    return {"ok": True}


def _validate_common(settings: dict) -> None:
    """Gemeinsame Geräte-Einstellungen prüfen (Phasen, Grenzen, Wärmepuffer)."""
    def bad(msg: str) -> None:
        raise HTTPException(422, msg)

    # Preisgrenzen je Gerät gibt es nicht mehr (eine Grenze im Tarif) –
    # alte Felder werden still entfernt statt abgelehnt.
    settings.pop("price_limit_mode", None)
    settings.pop("price_limit_ct", None)
    phases = settings.get("phases_mode")
    if phases is not None and phases not in ("fixed1", "fixed3", "auto"):
        bad("Phasen müssen 'fixed1', 'fixed3' oder 'auto' sein.")
    for key, low, high, label, unit in (
        ("min_current", 1, 32, "Mindeststrom", "A"),
        ("max_current", 1, 63, "Höchststrom", "A"),
        ("start_threshold_w", 0, 50000, "Startschwelle", "W"),
        ("max_power_w", 0, 50000, "Höchstleistung", "W"),
        ("target_temp_c", 20, 85, "Zieltemperatur", "°C"),
        ("surplus_temp_c", 0, 80, "Wärmepuffer-Temperatur", "°C"),
        ("target_soc", 10, 100, "Ziel-Ladestand", "%"),
    ):
        if settings.get(key) is None:
            continue
        try:
            value = float(settings[key])
        except (TypeError, ValueError):
            bad(f"{label}: keine Zahl")
        if not low <= value <= high:
            bad(f"{label} muss zwischen {low} und {high} {unit} liegen.")
    if settings.get("min_current") is not None and settings.get("max_current") is not None:
        if float(settings["min_current"]) > float(settings["max_current"]):
            bad("Mindeststrom darf nicht über dem Höchststrom liegen.")


def _validate_boost(settings: dict) -> None:
    """Boost-Abschaltbedingung prüfen – klare Meldung statt stillem Kappen.
    (Der Regler kappt zusätzlich selbst, falls Altdaten außerhalb liegen.)"""
    from ..core.regulation import BOOST_END_MODES, BOOST_MAX_MIN, BOOST_TEMP_MAX_C, BOOST_TEMP_MIN_C

    mode = settings.get("boost_end_mode")
    if mode is not None and mode not in BOOST_END_MODES:
        raise HTTPException(422,
                            "Boost-Abschaltung muss 'time', 'temp' oder 'both' sein.")
    checks = (
        ("boost_temp_c", BOOST_TEMP_MIN_C, BOOST_TEMP_MAX_C, "Boost-Zieltemperatur", "°C"),
        ("boost_duration_min", 5, BOOST_MAX_MIN, "Boost-Dauer", "min"),
        ("boost_max_min", 5, BOOST_MAX_MIN, "Boost-Sicherheitsgrenze", "min"),
    )
    for key, low, high, label, unit in checks:
        if settings.get(key) is None:
            continue
        try:
            value = float(settings[key])
        except (TypeError, ValueError):
            raise HTTPException(422, f"{label}: keine Zahl")
        if not low <= value <= high:
            raise HTTPException(422,
                                f"{label} muss zwischen {low:.0f} und {high:.0f} {unit} liegen.")


#: Konfigurationsfelder, die eine physische Adresse beschreiben
_ENDPOINT_KEYS = ("host", "port", "unit_id", "serial_port", "url", "ip", "vin")


async def _find_duplicate(db: AsyncSession, driver_id: str, config: dict) -> Device | None:
    """Gleicher Treiber + gleiche Adresse = dasselbe Gerät. Treiber ohne
    Adressfelder (Simulation) werden nicht geprüft."""
    key = {k: str(config.get(k)).strip() for k in _ENDPOINT_KEYS if config.get(k) not in (None, "")}
    if not key or ("host" not in key and "url" not in key and "vin" not in key and "serial_port" not in key):
        return None
    rows = (await db.scalars(select(Device).where(Device.driver_id == driver_id))).all()
    for row in rows:
        other = {k: str((row.config or {}).get(k)).strip()
                 for k in _ENDPOINT_KEYS if (row.config or {}).get(k) not in (None, "")}
        if other == key:
            return row
    return None


async def _set_charge_limit(dev, device_id: int, value) -> None:
    """Ladegrenze im Fahrzeug setzen (z. B. 80 % statt 100 %).

    Dauerhaft auf 100 % zu laden verkürzt die Lebensdauer von Lithium-Akkus
    spürbar. Der Wert wird im Fahrzeug selbst gesetzt – MinePower merkt ihn
    sich zusätzlich, damit die Oberfläche ihn auch dann kennt, wenn das
    Fahrzeug gerade schläft und keine Daten liefert."""
    try:
        limit = int(float(value))
    except (TypeError, ValueError):
        raise HTTPException(422, f"Ungültige Ladegrenze: {value}")
    if not 50 <= limit <= 100:
        raise HTTPException(
            422,
            "Ladegrenze muss zwischen 50 % und 100 % liegen.",
        )
    try:
        await dev.driver.set_charge_limit(limit)
    except NotImplementedError:
        raise HTTPException(
            422,
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
