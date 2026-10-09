"""Datenmodell (siehe Spezifikation §9).

`measurements` wird beim Erststart in eine TimescaleDB-Hypertable umgewandelt
(siehe app/startup.py) inkl. Retention-Policy.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _current_version() -> str:
    from . import __version__

    return __version__


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    # Rollen: admin | user | readonly
    role: Mapped[str] = mapped_column(String(20), default="user")
    rfid_tag: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    language: Mapped[str] = mapped_column(String(5), default="de")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: Zuletzt gesehene Version (Hinweisfenster nach Updates). Neue Konten
    #: starten mit der laufenden Version – nach einer Neuinstallation gibt es
    #: also kein Hinweisfenster.
    last_seen_version: Mapped[str | None] = mapped_column(String(20), nullable=True, default=_current_version)


class Device(Base):
    """Ein konfiguriertes Gerät. `driver_id` referenziert einen registrierten
    Treiber (drivers/registry.py); `config` enthält dessen Verbindungsdaten,
    `settings` die regelungsrelevanten Parameter (Schwellen, Min/Max, Modus …)."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    # Kategorie: inverter | meter | wallbox | water_heater | battery
    category: Mapped[str] = mapped_column(String(30), index=True)
    driver_id: Mapped[str] = mapped_column(String(60))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Laufzeitstatus (persistiert für Anzeige nach Neustart)
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # online|offline|error|unknown
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """Globale Key-Value-Einstellungen (JSON-Werte). Zentrale Keys:
    regulation (Intervall, Deadband, Puffer, Glättung),
    priority (geordnete Device-IDs), grid (Hausanschluss-Limit),
    tariff, forecast, notifications, mqtt."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    capacity_kwh: Mapped[float] = mapped_column(Float, default=60.0)
    # Standard-Wallbox dieses Fahrzeugs (optional)
    wallbox_device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    # SoC-Quelle: none | wallbox | driver (z. B. Tesla-Treiber liefert SoC)
    soc_source: Mapped[str] = mapped_column(String(20), default="none")
    default_target_soc: Mapped[int] = mapped_column(Integer, default=80)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Schedule(Base):
    """Zeitfenster für Modus 'schedule' (und Warmwasser-Zeitpläne)."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    # Bitmaske Mo=1 … So=64, 127 = täglich
    days_mask: Mapped[int] = mapped_column(Integer, default=127)
    start_time: Mapped[str] = mapped_column(String(5))  # "22:00"
    end_time: Mapped[str] = mapped_column(String(5))    # "06:00"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ChargeSession(Base):
    __tablename__ = "charge_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rfid_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    energy_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    solar_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    cost_eur: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price_ct: Mapped[float | None] = mapped_column(Float, nullable=True)


class Measurement(Base):
    """Zeitreihen-Messwerte → TimescaleDB-Hypertable.

    Schmales Schema (source, field, value) für beliebige Größen:
    field ∈ {pv_power, grid_power, battery_power, battery_soc, house_power,
             wallbox_power, water_power, vehicle_soc, surplus, price_ct, …}
    """

    __tablename__ = "measurements"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=utcnow)
    source: Mapped[str] = mapped_column(String(60), primary_key=True)
    field: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[float] = mapped_column(Float)

    __table_args__ = (Index("ix_measurements_field_time", "field", "time"),)


class Event(Base):
    """Audit-Log / Ereignis-Historie."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")  # info|warning|error
    category: Mapped[str] = mapped_column(String(30), default="system")  # system|control|device|auth|config
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
