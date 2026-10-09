"""Simulations-/Demo-Treiber: komplette Anlage ohne Hardware.

Alle Sim-Treiber teilen sich eine `SimulationWorld`, damit die Physik
konsistent ist: PV-Kurve nach Tageszeit + Wetter-Rauschen, Hauslast mit
Verbrauchsspitzen, Batterie mit Eigenverbrauchs-Logik, Wallbox + Fahrzeug-SoC,
Warmwasser mit Temperaturmodell. Der Netzzähler ergibt sich aus der Bilanz.
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime

from .base import (
    BatteryData,
    BatteryDriver,
    BatteryMode,
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    InverterData,
    InverterDriver,
    Maturity,
    MeterData,
    MeterDriver,
    WallboxData,
    WallboxDriver,
    WallboxState,
    WaterHeaterData,
    WaterHeaterDriver,
)
from .registry import register


class SimulationWorld:
    """Gemeinsamer Zustand aller Simulations-Treiber (Singleton)."""

    _instance: "SimulationWorld | None" = None

    @classmethod
    def instance(cls) -> "SimulationWorld":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self.kwp = 9.8
        self.weather = 0.85          # 0..1, langsamer Random-Walk
        self.house_base = 280.0      # W Grundlast
        self.house_spike = 0.0       # W aktueller Verbrauchs-Peak
        self._spike_until = 0.0

        # Batterie
        self.bat_capacity_kwh = 9.6
        self.bat_soc = 55.0
        self.bat_power = 0.0         # + laden / − entladen
        self.bat_mode = BatteryMode.AUTO
        self.bat_force_w = 0.0
        self.bat_reserve_soc = 10.0
        self.bat_max_charge = 5000.0
        self.bat_max_discharge = 5000.0

        # Wallbox / Fahrzeug
        self.car_connected = True
        self.car_capacity_kwh = 62.0
        self.car_soc = 42.0
        self.car_charge_limit = 80.0   # % – wie im echten Fahrzeug hinterlegt
        self.wb_enabled = False
        self.wb_current = 6.0
        self.wb_phases = 1
        self.wb_max_current = 16.0
        self.wb_power = 0.0
        self.wb_session_kwh = 0.0

        # Szenarien für Demo und Oberflächentests (siehe api/system.py
        # 'demo/scenario"): Fahrzeug schläft/unterwegs, BLE-Proxy aus,
        # Heizstab im Geräteprogramm.
        self.car_reachable = True
        self.car_proxy_offline = False
        self.wh_device_program = False

        # Warmwasser
        self.wh_rated = 3000.0
        self.wh_set_power = 0.0
        self.wh_power = 0.0
        self.wh_temp = 44.0

        self._last_tick = time.monotonic()

    # ------------------------------------------------------------ Physik

    def tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_tick, 30.0)
        if dt < 0.25:
            return
        self._last_tick = now
        h = dt / 3600.0

        # Wetter: träger Random-Walk
        self.weather = min(1.0, max(0.2, self.weather + random.uniform(-0.02, 0.02)))

        # Hauslast-Spitzen (Herd, Wasserkocher …)
        if now > self._spike_until:
            if random.random() < 0.02:
                self.house_spike = random.choice([800.0, 1500.0, 2200.0])
                self._spike_until = now + random.uniform(60, 300)
            else:
                self.house_spike = 0.0

        # Wallbox-Leistung
        if self.car_connected and self.wb_enabled and self.car_soc < self.car_charge_limit:
            self.wb_power = self.wb_current * self.wb_phases * 230.0 * 0.93
        else:
            self.wb_power = 0.0
        self.car_soc = min(self.car_charge_limit,
                           self.car_soc + (self.wb_power * h) / (self.car_capacity_kwh * 1000.0) * 100.0)
        self.wb_session_kwh += self.wb_power * h / 1000.0

        # Warmwasser: Leistung folgt Sollwert, Temperaturmodell (180 l Speicher)
        if self.wh_device_program:
            self.wh_power = 2980.0 if self.wh_temp < 75.0 else 0.0
        else:
            self.wh_power = min(self.wh_set_power, self.wh_rated) if self.wh_temp < 72.0 else 0.0
        self.wh_temp += (self.wh_power * h) / (180 * 1.163 / 1000.0)  # kWh je K für 180 l
        self.wh_temp = max(20.0, self.wh_temp - 0.15 * h * 24)        # Standverlust

        # Batterie reagiert auf Residual (Eigenverbrauchsoptimierung)
        residual = self.pv_power() - self.house_load() - self.wb_power - self.wh_power
        if self.bat_mode == BatteryMode.FORCE_CHARGE:
            target = min(self.bat_force_w or self.bat_max_charge, self.bat_max_charge)
        elif self.bat_mode == BatteryMode.FORCE_DISCHARGE:
            target = -min(self.bat_force_w or self.bat_max_discharge, self.bat_max_discharge)
        elif self.bat_mode == BatteryMode.HOLD:
            target = 0.0
        else:  # AUTO
            if residual > 0 and self.bat_soc < 100.0:
                target = min(residual, self.bat_max_charge)
            elif residual < 0 and self.bat_soc > self.bat_reserve_soc:
                target = max(residual, -self.bat_max_discharge)
            else:
                target = 0.0
        if target > 0 and self.bat_soc >= 100.0:
            target = 0.0
        if target < 0 and self.bat_soc <= self.bat_reserve_soc:
            target = 0.0
        # sanfte Annäherung an Zielleistung
        self.bat_power += (target - self.bat_power) * min(1.0, dt / 5.0)
        self.bat_soc = min(100.0, max(0.0, self.bat_soc + (self.bat_power * h) / (self.bat_capacity_kwh * 1000.0) * 100.0))

    def pv_power(self) -> float:
        t = datetime.now()
        hour = t.hour + t.minute / 60.0
        # Glockenkurve: Sonnenaufgang ~6:30, Peak ~13:00, Untergang ~20:30
        x = (hour - 13.0) / 5.0
        clear_sky = math.exp(-x * x * 2.2)
        if hour < 5.5 or hour > 21.0:
            clear_sky = 0.0
        flicker = 1.0 + random.uniform(-0.03, 0.03)
        return max(0.0, self.kwp * 1000.0 * clear_sky * self.weather * flicker)

    def house_load(self) -> float:
        return self.house_base + self.house_spike + random.uniform(-15, 15)

    def grid_power(self) -> float:
        """+ Bezug / − Einspeisung (Bilanz aller Simulationsgrößen)."""
        return (
            self.house_load() + self.wb_power + self.wh_power + self.bat_power - self.pv_power()
        )


def _world() -> SimulationWorld:
    w = SimulationWorld.instance()
    w.tick()
    return w


@register
class SimInverter(InverterDriver):
    meta = DriverMeta(
        id="sim_inverter",
        name="Simulation: PV-Wechselrichter",
        category=DeviceCategory.INVERTER,
        maturity=Maturity.STABLE,
        description="Simulierter Wechselrichter mit realistischer Tageskurve und Wetter-Rauschen.",
        fields=[
            ConfigField(key="kwp", label="Anlagengröße (kWp)", type=FieldType.NUMBER, default=9.8,
                        help="Peak-Leistung der simulierten PV-Anlage."),
        ],
    )

    async def connect(self) -> None:
        SimulationWorld.instance().kwp = float(self.config.get("kwp") or 9.8)

    async def read_data(self) -> InverterData:
        w = _world()
        return InverterData(pv_power=round(w.pv_power(), 1), status="ok")


@register
class SimMeter(MeterDriver):
    meta = DriverMeta(
        id="sim_meter",
        name="Simulation: Netzzähler",
        category=DeviceCategory.METER,
        maturity=Maturity.STABLE,
        description="Simulierter Zähler am Hausanschluss – ergibt sich aus der Energiebilanz der Simulation.",
    )

    async def read_data(self) -> MeterData:
        w = _world()
        p = round(w.grid_power(), 1)
        return MeterData(grid_power=p, power_l1=p / 3, power_l2=p / 3, power_l3=p / 3)


@register
class SimWallbox(WallboxDriver):
    meta = DriverMeta(
        id="sim_wallbox",
        name="Simulation: Wallbox + E-Auto",
        category=DeviceCategory.WALLBOX,
        maturity=Maturity.STABLE,
        description="Simulierte 11-kW-Wallbox mit verbundenem Fahrzeug (SoC-Modell, Phasenumschaltung).",
        capabilities={"phase_switch", "soc", "charge_limit"},
        fields=[
            ConfigField(key="max_current", label="Max. Ladestrom (A)", type=FieldType.NUMBER, default=16,
                        help="Hardware-Limit der simulierten Wallbox."),
            ConfigField(key="car_capacity_kwh", label="Fahrzeug-Akku (kWh)", type=FieldType.NUMBER, default=62,
                        help="Kapazität des simulierten Fahrzeugs."),
        ],
    )

    min_current = 6.0

    @property
    def likely_plugged_in(self) -> bool:
        return SimulationWorld.instance().car_connected

    async def connect(self) -> None:
        w = SimulationWorld.instance()
        w.wb_max_current = float(self.config.get("max_current") or 16)
        w.car_capacity_kwh = float(self.config.get("car_capacity_kwh") or 62)
        self.max_current = w.wb_max_current

    async def read_data(self) -> WallboxData:
        w = _world()
        if w.car_proxy_offline:
            raise ConnectionError("Tesla-Endpunkt http://192.0.2.33:8080 nicht erreichbar – läuft der Proxy?")
        if not w.car_reachable:
            return WallboxData(
                state=WallboxState.CONNECTED if w.car_connected else WallboxState.IDLE,
                power=0.0, soc=round(w.car_soc, 1), charge_limit_soc=w.car_charge_limit,
                vehicle_reachable=False,
            )
        if not w.car_connected:
            state = WallboxState.IDLE
        elif w.car_soc >= w.car_charge_limit:
            state = WallboxState.COMPLETE
        elif w.wb_enabled and w.wb_power > 0:
            state = WallboxState.CHARGING
        else:
            state = WallboxState.CONNECTED
        return WallboxData(
            state=state,
            power=round(w.wb_power, 1),
            current_set=w.wb_current,
            phases_active=w.wb_phases,
            energy_session_kwh=round(w.wb_session_kwh, 3),
            soc=round(w.car_soc, 1),
            charge_limit_soc=w.car_charge_limit,
        )

    async def set_current(self, amps: float) -> None:
        w = SimulationWorld.instance()
        w.wb_current = max(0.0, min(amps, w.wb_max_current))
        if w.wb_current < self.min_current:
            w.wb_enabled = False

    async def start_charging(self) -> None:
        SimulationWorld.instance().wb_enabled = True

    async def stop_charging(self) -> None:
        SimulationWorld.instance().wb_enabled = False

    async def set_phases(self, phases: int) -> None:
        SimulationWorld.instance().wb_phases = 3 if phases >= 2 else 1

    async def set_charge_limit(self, soc: int) -> None:
        SimulationWorld.instance().car_charge_limit = float(max(50, min(100, soc)))


@register
class SimWaterHeater(WaterHeaterDriver):
    meta = DriverMeta(
        id="sim_water_heater",
        name="Simulation: Heizstab / Warmwasser",
        category=DeviceCategory.WATER_HEATER,
        maturity=Maturity.STABLE,
        description="Simulierter modulierender Heizstab (0–3 kW) mit 180-l-Speicher-Temperaturmodell.",
        capabilities={"modulation", "temperature"},
        fields=[
            ConfigField(key="rated_power", label="Nennleistung (W)", type=FieldType.NUMBER, default=3000,
                        help="Maximale Heizleistung des simulierten Heizstabs."),
        ],
    )

    async def connect(self) -> None:
        w = SimulationWorld.instance()
        w.wh_rated = float(self.config.get("rated_power") or 3000)
        self.rated_power = w.wh_rated

    async def read_data(self) -> WaterHeaterData:
        w = _world()
        return WaterHeaterData(
            power=round(w.wh_power, 1), temperature_c=round(w.wh_temp, 1), is_on=w.wh_power > 0,
            device_mode="Warmwasser-Sicherstellung (Simulation)" if w.wh_device_program and w.wh_power > 0 else None,
        )

    async def set_power(self, watts: float) -> None:
        w = SimulationWorld.instance()
        w.wh_set_power = max(0.0, min(watts, w.wh_rated))


@register
class SimBattery(BatteryDriver):
    meta = DriverMeta(
        id="sim_battery",
        name="Simulation: Hausbatterie",
        category=DeviceCategory.BATTERY,
        maturity=Maturity.STABLE,
        description="Simulierter Speicher mit Eigenverbrauchs-Logik, steuerbar (Zwangsladen/-entladen, Reserve).",
        capabilities={"battery_control"},
        fields=[
            ConfigField(key="capacity_kwh", label="Kapazität (kWh)", type=FieldType.NUMBER, default=9.6,
                        help="Nutzbare Kapazität des simulierten Speichers."),
            ConfigField(key="max_power", label="Max. Lade-/Entladeleistung (W)", type=FieldType.NUMBER, default=5000,
                        help="Leistungsgrenze in beide Richtungen."),
        ],
    )

    async def connect(self) -> None:
        w = SimulationWorld.instance()
        w.bat_capacity_kwh = float(self.config.get("capacity_kwh") or 9.6)
        w.bat_max_charge = w.bat_max_discharge = float(self.config.get("max_power") or 5000)

    async def read_data(self) -> BatteryData:
        w = _world()
        return BatteryData(
            soc=round(w.bat_soc, 1),
            power=round(w.bat_power, 1),
            mode=w.bat_mode,
            mode_known=True,
            capacity_kwh=w.bat_capacity_kwh,
            status="ok",
        )

    async def set_mode(self, mode: BatteryMode, power_w: float | None = None) -> None:
        w = SimulationWorld.instance()
        w.bat_mode = mode
        w.bat_force_w = float(power_w or 0.0)

    async def set_reserve_soc(self, soc: int) -> None:
        SimulationWorld.instance().bat_reserve_soc = float(soc)
