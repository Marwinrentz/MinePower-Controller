"""Pydantic-Schemas für die REST-API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# --- Auth ---

class LoginRequest(BaseModel):
    # bewusst str statt EmailStr: beim Login ist die Adresse nur Lookup-Key,
    # strenge Validierung würde z. B. .local-Demo-Konten aussperren
    email: str
    password: str


class TokenResponse(BaseModel):
    token: str
    user: "UserOut"


class SetupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = "Admin"
    language: str = "de"


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str
    role: str = "user"
    rfid_tag: str | None = None
    language: str = "de"


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    rfid_tag: str | None = None
    language: str | None = None
    password: str | None = Field(default=None, min_length=8)
    disabled: bool | None = None


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    rfid_tag: str | None = None
    language: str
    disabled: bool
    last_seen_version: str | None = None

    class Config:
        from_attributes = True


# --- Geräte ---

class DeviceCreate(BaseModel):
    name: str
    category: str
    driver_id: str
    config: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class DeviceUpdate(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    enabled: bool | None = None


class DeviceOut(BaseModel):
    id: int
    name: str
    category: str
    driver_id: str
    config: dict[str, Any]
    settings: dict[str, Any]
    enabled: bool
    status: str
    last_error: str | None
    last_seen: datetime | None

    class Config:
        from_attributes = True


class TestConnectionRequest(BaseModel):
    driver_id: str
    config: dict[str, Any] = Field(default_factory=dict)
    #: Beim Prüfen eines gespeicherten Geräts: '•••" in `config` wird durch
    #: die gespeicherten Zugangsdaten ersetzt.
    device_id: int | None = None
    #: Zusätzlich einen unkritischen Testbefehl senden und das Readback zeigen
    #: (z. B. Ladestrom setzen). Nur auf ausdrücklichen Wunsch – ein Schreibtest
    #: greift, wenn auch harmlos, in ein laufendes Gerät ein.
    include_write: bool = False


class DeviceActionRequest(BaseModel):
    """Manueller Override: action ∈ {fast, stop, auto, boost, boost_off,
    set_mode, set_current, battery_mode}."""

    action: str
    value: Any = None


# --- Einstellungen ---

class SettingsOut(BaseModel):
    values: dict[str, Any]


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class PriorityUpdate(BaseModel):
    order: list[int]


class ScheduleCreate(BaseModel):
    device_id: int
    days_mask: int = 127
    start_time: str
    end_time: str
    enabled: bool = True


class ScheduleOut(ScheduleCreate):
    id: int

    class Config:
        from_attributes = True


# --- Fahrzeuge ---

class VehicleCreate(BaseModel):
    name: str
    capacity_kwh: float = 60.0
    wallbox_device_id: int | None = None
    soc_source: str = "none"
    default_target_soc: int = 80


class VehicleOut(VehicleCreate):
    id: int

    class Config:
        from_attributes = True


# --- Historie / Sessions / Events ---

class HistoryPoint(BaseModel):
    time: datetime
    value: float


class ChargeSessionOut(BaseModel):
    id: int
    device_id: int
    vehicle_id: int | None
    user_id: int | None
    rfid_tag: str | None
    started_at: datetime
    ended_at: datetime | None
    energy_kwh: float
    solar_kwh: float
    cost_eur: float

    class Config:
        from_attributes = True


class EventOut(BaseModel):
    id: int
    time: datetime
    level: str
    category: str
    message: str
    data: dict[str, Any]

    class Config:
        from_attributes = True
