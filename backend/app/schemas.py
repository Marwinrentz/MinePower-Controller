"""Pydantic-Schemas für die REST-API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


# --- Auth ---

#: Obergrenze für Passwörter (bcrypt wertet 72 Byte aus; mehr anzunehmen
#: kostet nur Rechenzeit).
PASSWORD_MAX = 256
Role = Literal["admin", "user", "readonly"]


class LoginRequest(BaseModel):
    # bewusst str statt EmailStr: beim Login ist die Adresse nur Lookup-Key,
    # strenge Validierung würde z. B. .local-Demo-Konten aussperren
    email: str = Field(max_length=255)
    password: str = Field(max_length=PASSWORD_MAX)


class TokenResponse(BaseModel):
    token: str
    user: "UserOut"


class SetupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=PASSWORD_MAX)
    name: str = Field(default="Admin", min_length=1, max_length=120)
    language: str = Field(default="de", max_length=5)


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=PASSWORD_MAX)
    name: str = Field(min_length=1, max_length=120)
    role: Role = "user"
    rfid_tag: str | None = Field(default=None, max_length=64)
    language: str = Field(default="de", max_length=5)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    rfid_tag: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=5)
    password: str | None = Field(default=None, min_length=8, max_length=PASSWORD_MAX)
    disabled: bool | None = None


class PasswordChange(BaseModel):
    """Eigenes Passwort ändern: nur mit dem bisherigen."""

    current_password: str = Field(max_length=PASSWORD_MAX)
    new_password: str = Field(min_length=8, max_length=PASSWORD_MAX)


class PrefsUpdate(BaseModel):
    """Persönliche Einstellungen der Oberfläche. Nur bekannte Schlüssel."""

    beta_ui: bool | None = None


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    rfid_tag: str | None = None
    language: str
    disabled: bool
    last_seen_version: str | None = None
    prefs: dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True

    @field_validator("prefs", mode="before")
    @classmethod
    def _prefs_none(cls, value: Any) -> Any:
        return value or {}


# --- Geräte ---

class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(max_length=30)
    driver_id: str = Field(max_length=60)
    config: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
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
    driver_id: str = Field(max_length=60)
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

    action: str = Field(max_length=40)
    value: Any = None


# --- Einstellungen ---

class SettingsOut(BaseModel):
    values: dict[str, Any]


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class PriorityUpdate(BaseModel):
    order: list[int] = Field(max_length=200)


class ScheduleCreate(BaseModel):
    device_id: int
    days_mask: int = 127
    start_time: str = Field(max_length=5)
    end_time: str = Field(max_length=5)
    enabled: bool = True


class ScheduleOut(BaseModel):
    # Eigenes Modell ohne Eingabegrenzen: Bestandsdaten dürfen die Antwort
    # nicht scheitern lassen.
    id: int
    device_id: int
    days_mask: int
    start_time: str
    end_time: str
    enabled: bool

    class Config:
        from_attributes = True


# --- Fahrzeuge ---

class VehicleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    capacity_kwh: float = Field(default=60.0, gt=0, le=1000)
    wallbox_device_id: int | None = None
    soc_source: str = Field(default="none", max_length=20)
    default_target_soc: int = Field(default=80, ge=10, le=100)


class VehicleOut(BaseModel):
    id: int
    name: str
    capacity_kwh: float
    wallbox_device_id: int | None = None
    soc_source: str
    default_target_soc: int

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
