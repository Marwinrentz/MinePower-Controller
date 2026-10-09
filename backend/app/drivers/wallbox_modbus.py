"""Wallboxen mit Modbus-Schnittstelle (TCP oder RS485 über ein TCP-Gateway).

Jede Box hat eine eigene Registerkarte; die Abläufe sind aber ähnlich:
Zustand lesen (IEC 61851 A–F), Leistung und Phasenströme lesen, Ladestrom
schreiben, Laden freigeben oder sperren. Boxen mit Watchdog erwarten
regelmäßige Lebenszeichen, sonst fallen sie auf ihren Sicherheitsstrom
zurück. Diese sendet ein Hintergrund-Task (siehe wallbox_common).

Alle Treiber sind experimentell: nach Herstellerdokumentation bzw. der
Referenzimplementierung in evcc geschrieben, nicht an Hardware geprüft.
"""
from __future__ import annotations

import struct

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WallboxData, WallboxState
from .modbus_util import f32, s16, s32, u32
from .registry import register
from .validation import DriverError
from .wallbox_common import (
    MAX_CURRENT_FIELD, PHASES_FIELD, LimitWallbox, ModbusWallbox, f, iec_state, modbus_fields, regs_text,
    wallbox_data,
)

WALLBOX = DeviceCategory.WALLBOX
CAPS = {"write_test"}


def _f32s(regs: list[int], count: int = 3) -> list[float]:
    out = []
    for i in range(count):
        v = f32(regs, 2 * i)
        out.append(0.0 if v != v else float(v))  # NaN → 0
    return out


def _float_regs(value: float) -> list[int]:
    hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
    return [hi, lo]


def _estimate(state: WallboxState, amps: float, phases: int) -> float:
    """Leistung schätzen, wenn das Gerät keinen Zähler hat (230 V je Phase)."""
    return amps * phases * 230.0 if state == WallboxState.CHARGING else 0.0


# ---------------------------------------------------------------- cFos

@register
class CfosPowerBrain(ModbusWallbox):
    meta = DriverMeta(
        id="cfos_powerbrain", name="cFos Power Brain (Modbus TCP)", category=WALLBOX,
        description="cFos Power Brain Wallbox bzw. Controller über Modbus TCP.",
        capabilities=CAPS,
        notes="Lastmanagement im Power Brain auf 'Beobachten' stellen, sonst überschreibt es die Vorgaben.",
        fields=[*modbus_fields(port=4701, unit=1), MAX_CURRENT_FIELD],
    )

    async def read_data(self) -> WallboxData:
        status = (await self.conn.read_holding(8092, 1))[0] & 0xFF
        if status == 5:
            raise DriverError("cFos meldet Übertemperatur")
        state = {0: WallboxState.IDLE, 1: WallboxState.CONNECTED, 2: WallboxState.CHARGING}.get(
            status, WallboxState.ERROR)
        amps = (await self.conn.read_holding(8093, 1))[0] / 10.0
        enabled = ((await self.conn.read_holding(8094, 1))[0] & 0xFF) == 1
        power = u32(await self.conn.read_holding(8062, 2), word_order="big")
        cur = await self.conn.read_holding(8064, 6)
        currents = [u32(cur, 2 * i, word_order="big") / 10.0 for i in range(3)]
        return wallbox_data(state, power, current_set=amps if enabled else 0.0, currents=currents,
                            source="cFos 8062")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self.conn.write_register(8093, int(round(self.clamp(amps) * 10)), verify=True)

    async def start_charging(self) -> None:
        await self.conn.write_register(8094, 1, verify=True)

    async def stop_charging(self) -> None:
        await self.conn.write_register(8094, 0, verify=True)


# ---------------------------------------------------------------- Alfen

@register
class AlfenEve(LimitWallbox):
    meta = DriverMeta(
        id="alfen_eve", name="Alfen Eve (Modbus TCP)", category=WALLBOX,
        description="Alfen Eve Single/Double Pro-line über Modbus TCP.",
        capabilities=CAPS,
        notes="Im ACE Service Installer: Active Load Balancing aktiv, Datenquelle 'Energy Management System'. "
              "Unit-ID 1 = Ladepunkt 1, 2 = Ladepunkt 2.",
        fields=[*modbus_fields(unit=1), MAX_CURRENT_FIELD],
    )
    #: Der Ladestrom gilt bei Alfen nur 60 s und muss aufgefrischt werden.
    heartbeat_s = 25.0

    async def heartbeat(self) -> None:
        await self.write_limit(self._amps if self._enabled else 0)

    async def write_limit(self, amps: float) -> None:
        await self.conn.write_registers(1210, _float_regs(amps))

    async def read_data(self) -> WallboxData:
        state = iec_state(regs_text(await self.conn.read_holding(1201, 5)))
        limit = f32(await self.conn.read_holding(1210, 2))
        power = f32(await self.conn.read_holding(344, 2))
        currents = _f32s(await self.conn.read_holding(320, 6))
        voltage = _f32s(await self.conn.read_holding(306, 2), 1)[0]
        return wallbox_data(state, 0.0 if power != power else power, current_set=limit if limit == limit else None,
                            currents=currents, voltage=voltage, source="Alfen 344")


# ---------------------------------------------------------------- Webasto Next, Vestel

class _VestelFamily(LimitWallbox):
    """Webasto Next/Ampure Unite basieren auf Vestel EVC04: gleiche Register."""

    heartbeat_s = 5.0

    async def heartbeat(self) -> None:
        await self.conn.write_register(6000, 1)

    async def write_limit(self, amps: float) -> None:
        await self.conn.write_register(5004, int(round(amps)))

    async def _currents(self) -> list[float]:
        out = []
        for reg in (1008, 1010, 1012):
            out.append((await self.conn.read_input(reg, 1))[0] / 1000.0)
        return out


@register
class WebastoNext(_VestelFamily):
    meta = DriverMeta(
        id="webasto_next", name="Webasto Next / Ampure Unite (Modbus TCP)", category=WALLBOX,
        description="Webasto Next und Ampure Unite über Modbus TCP.",
        capabilities=CAPS,
        notes="Modbus in der Web-Oberfläche der Box aktivieren (HEMS).",
        fields=[*modbus_fields(unit=255), MAX_CURRENT_FIELD],
    )

    async def read_data(self) -> WallboxData:
        code = (await self.conn.read_holding(1000, 1))[0]
        state = {0: WallboxState.IDLE, 1: WallboxState.CONNECTED, 2: WallboxState.CONNECTED,
                 3: WallboxState.CHARGING, 4: WallboxState.CONNECTED}.get(code, WallboxState.ERROR)
        power = u32(await self.conn.read_holding(1020, 2), word_order="big")
        return wallbox_data(state, power, current_set=self._amps if self._enabled else 0.0,
                            currents=await self._currents(), source="Webasto 1020")


@register
class VestelEvc04(_VestelFamily):
    meta = DriverMeta(
        id="vestel_evc04", name="Vestel EVC04 (Modbus TCP)", category=WALLBOX,
        description="Vestel EVC04 und baugleiche Boxen (z. B. Hager witty start) über Modbus TCP.",
        capabilities=CAPS,
        notes="Modbus TCP in der Web-Oberfläche aktivieren.",
        fields=[*modbus_fields(unit=255), MAX_CURRENT_FIELD],
    )

    async def read_data(self) -> WallboxData:
        cable = (await self.conn.read_input(1004, 1))[0]
        charging = (await self.conn.read_input(1001, 1))[0] == 1
        state = (WallboxState.CHARGING if charging else WallboxState.CONNECTED) if cable >= 2 else WallboxState.IDLE
        power = u32(await self.conn.read_input(1020, 2), word_order="big")
        session = u32(await self.conn.read_input(1502, 2), word_order="big") / 1000.0
        return wallbox_data(state, power, current_set=self._amps if self._enabled else 0.0,
                            currents=await self._currents(), energy_session_kwh=session, source="Vestel 1020")


# ---------------------------------------------------------------- Bender / Mennekes Amtron

@register
class BenderCC(LimitWallbox):
    meta = DriverMeta(
        id="bender_cc", name="Bender CC612/CC613, Mennekes Amtron (Modbus TCP)", category=WALLBOX,
        description="Ladecontroller Bender CC612/CC613, u. a. in Mennekes Amtron Xtra/Premium/4You/4Business, "
                    "Walther, Ubitricity.",
        capabilities=CAPS,
        notes="In der Web-Oberfläche: Modbus TCP Server aktiv, Register-Adresssatz 'Ebee', "
              "HEMS-Strombegrenzung zulassen.",
        fields=[*modbus_fields(unit=255), MAX_CURRENT_FIELD, PHASES_FIELD],
    )

    async def write_limit(self, amps: float) -> None:
        await self.conn.write_registers(1000, [int(round(amps))])

    async def read_data(self) -> WallboxData:
        code = (await self.conn.read_holding(122, 1))[0]
        state = {1: WallboxState.IDLE, 2: WallboxState.CONNECTED, 3: WallboxState.CHARGING,
                 4: WallboxState.CHARGING}.get(code, WallboxState.ERROR)
        cur = await self.conn.read_holding(212, 6)
        currents = [0.0 if u32(cur, 2 * i, "big") == 0xFFFFFFFF else u32(cur, 2 * i, "big") / 1000.0
                    for i in range(3)]
        raw = u32(await self.conn.read_holding(220, 2), word_order="big")
        power = sum(currents) * 230.0 if raw == 0xFFFFFFFF else float(raw)
        limit = (await self.conn.read_holding(1000, 1))[0]
        soc = None
        try:
            v = (await self.conn.read_holding(730, 1))[0]
            soc = float(v) if 0 < v <= 100 else None
        except DriverError:
            pass  # ältere Firmware ohne ISO-15118-Ladestand
        return wallbox_data(state, power, current_set=float(limit), currents=currents, soc=soc,
                            source="Bender 220")


# ---------------------------------------------------------------- Phoenix Contact

@register
class PhoenixCharx(LimitWallbox):
    meta = DriverMeta(
        id="phoenix_charx", name="Phoenix Contact CHARX SEC (Modbus TCP)", category=WALLBOX,
        description="Ladecontroller Phoenix Contact CHARX SEC-3xxx über Modbus TCP.",
        capabilities=CAPS,
        notes="Modbus-Server im CHARX aktivieren. Ladepunkt = Nummer des Ladecontrollers (1, 2 …).",
        fields=[*modbus_fields(unit=1),
                ConfigField(key="connector", label="Ladepunkt", type=FieldType.NUMBER, default=1),
                MAX_CURRENT_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.connector = int(f(config.get("connector"), 1) or 1)

    def reg(self, offset: int) -> int:
        return self.connector * 1000 + offset

    async def write_limit(self, amps: float) -> None:
        await self.conn.write_registers(self.reg(301), [int(round(amps))])

    async def read_data(self) -> WallboxData:
        state = iec_state(regs_text(await self.conn.read_holding(self.reg(299), 1)))
        limit = (await self.conn.read_holding(self.reg(301), 1))[0]
        power = s32(await self.conn.read_holding(self.reg(244), 2), word_order="big") / 1000.0
        cur = await self.conn.read_holding(self.reg(238), 6)
        currents = [s32(cur, 2 * i, "big") / 1000.0 for i in range(3)]
        session = u32(await self.conn.read_holding(self.reg(289), 2), word_order="big") / 1000.0
        soc_raw = (await self.conn.read_holding(self.reg(264), 1))[0]
        return wallbox_data(state, power, current_set=float(limit), currents=currents, energy_session_kwh=session,
                            soc=float(soc_raw) if 0 < soc_raw <= 100 else None, source="CHARX 244")


@register
class PhoenixEvEth(ModbusWallbox):
    meta = DriverMeta(
        id="phoenix_ev_eth", name="Phoenix Contact EV-CC/EV-ETH, Wallbe (Modbus TCP)", category=WALLBOX,
        description="Ladecontroller Phoenix Contact EV-CC-AC1-M3-…-ETH, u. a. in Wallbe Eco/Pro, ESL Walli.",
        capabilities=CAPS,
        notes="Ladefreigabe per DIP-Schalter auf 'extern' stellen.",
        fields=[*modbus_fields(unit=255), MAX_CURRENT_FIELD, PHASES_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._scale: float | None = None  # 1 = Ampere, 10 = 0,1 A (Wallbe mit neuer Firmware)

    async def _current_scale(self) -> float:
        if self._scale is None:
            raw = (await self.conn.read_holding(528, 1))[0]
            self._scale = 10.0 if raw >= 60 else 1.0
        return self._scale

    async def read_data(self) -> WallboxData:
        state = iec_state(chr((await self.conn.read_input(100, 1))[0] & 0xFF))
        enabled = (await self.conn.read_coils(400, 1))[0]
        limit = (await self.conn.read_holding(528, 1))[0] / await self._current_scale()
        power = s32(await self.conn.read_input(120, 2), word_order="little")
        cur = await self.conn.read_input(114, 6)
        currents = [float(s32(cur, 2 * i, "little")) for i in range(3)]
        session = u32(await self.conn.read_input(132, 2), word_order="little") / 1000.0
        return wallbox_data(state, power, current_set=limit if enabled else 0.0, currents=currents,
                            energy_session_kwh=session, source="EV-ETH 120")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        scale = await self._current_scale()
        await self.conn.write_register(528, int(round(self.clamp(amps) * scale)))

    async def start_charging(self) -> None:
        await self.conn.write_coil(400, True)

    async def stop_charging(self) -> None:
        await self.conn.write_coil(400, False)


# ---------------------------------------------------------------- KEBA (Modbus TCP)

@register
class KebaModbus(ModbusWallbox):
    meta = DriverMeta(
        id="keba_modbus", name="KEBA KeContact P30 x / P40 (Modbus TCP)", category=WALLBOX,
        description="KEBA KeContact P30 x-Serie und P40 über Modbus TCP.",
        capabilities=CAPS,
        notes="Modbus TCP in der Web-Oberfläche bzw. per DIP-Schalter aktivieren.",
        fields=[*modbus_fields(unit=255), MAX_CURRENT_FIELD],
    )
    heartbeat_s = 10.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._p40: bool | None = None
        self._amps = self.min_current
        self._timeout: int | None = None

    async def _is_p40(self) -> bool:
        if self._p40 is None:
            code = str(u32(await self.conn.read_holding(1016, 2), word_order="big"))
            self._p40 = len(code) == 7 and code.startswith("4")
        return self._p40

    async def heartbeat(self) -> None:
        if self._timeout is None:
            self._timeout = u32(await self.conn.read_holding(1602, 2), word_order="big")
        if self._timeout:
            await self.conn.write_register(5018, min(self._timeout, 0xFFFF))

    async def read_data(self) -> WallboxData:
        charging = u32(await self.conn.read_holding(1000, 2), word_order="big")
        cable = u32(await self.conn.read_holding(1004, 2), word_order="big")
        if charging == 4:
            state = WallboxState.ERROR
        elif cable < 5:
            state = WallboxState.IDLE
        elif charging == 3:
            state = WallboxState.CHARGING
        else:
            state = WallboxState.CONNECTED
        power = u32(await self.conn.read_holding(1020, 2), word_order="big") / 1000.0
        currents = []
        for reg in (1008, 1010, 1012):
            currents.append(u32(await self.conn.read_holding(reg, 2), word_order="big") / 1000.0)
        enabled = charging != 5
        return wallbox_data(state, power, current_set=self._amps if enabled else 0.0, currents=currents,
                            source="KEBA 1020")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        self._amps = self.clamp(amps)
        await self.conn.write_register(5004, int(self._amps * 1000))

    async def start_charging(self) -> None:
        if await self._is_p40():
            await self.conn.write_register(5004, int(self._amps * 1000))
        else:
            await self.conn.write_register(5014, 1)

    async def stop_charging(self) -> None:
        if await self._is_p40():
            await self.conn.write_register(5004, 0)
        else:
            await self.conn.write_register(5014, 0)


# ---------------------------------------------------------------- Heidelberg / Amperfied

class _Heidelberg(LimitWallbox):
    """Heidelberg Energy Control und Amperfied: gleiche Registerkarte, Strom in 0,1 A."""

    async def connect(self) -> None:
        await super().connect()
        # Standby der Box abschalten, sonst schläft sie ohne Fahrzeug ein und antwortet nicht
        await self.conn.write_registers(258, [4])

    async def write_limit(self, amps: float) -> None:
        await self.conn.write_registers(261, [int(round(amps * 10))])

    async def read_data(self) -> WallboxData:
        code = (await self.conn.read_input(5, 1))[0]
        state = {2: WallboxState.IDLE, 3: WallboxState.IDLE, 4: WallboxState.CONNECTED,
                 5: WallboxState.CONNECTED, 6: WallboxState.CHARGING, 7: WallboxState.CHARGING}.get(
            code, WallboxState.ERROR)
        if code == 10:
            # Fernsperre aktiv: aufheben, damit die Box Vorgaben annimmt
            await self.conn.write_registers(259, [1])
        currents = [v / 10.0 for v in await self.conn.read_input(6, 3)]
        voltage = float((await self.conn.read_input(10, 1))[0])
        power = float((await self.conn.read_input(14, 1))[0])
        limit = (await self.conn.read_holding(261, 1))[0] / 10.0
        return wallbox_data(state, power, current_set=limit, currents=currents, voltage=voltage,
                            source="Heidelberg 14")


@register
class HeidelbergEC(_Heidelberg):
    default_framer = "rtu"
    meta = DriverMeta(
        id="heidelberg_ec", name="Heidelberg Energy Control (Modbus RTU)", category=WALLBOX,
        description="Heidelberg Energy Control über RS485 und ein Modbus-TCP-Gateway.",
        capabilities=CAPS,
        notes="RS485: 19200 Baud, 8E1. Bus-Adresse per DIP-Schalter. Gateway in den transparenten "
              "Modus bzw. auf Modbus RTU über TCP stellen.",
        fields=[*modbus_fields(port=502, unit=1, gateway=True), MAX_CURRENT_FIELD],
    )


@register
class Amperfied(_Heidelberg):
    meta = DriverMeta(
        id="amperfied", name="Amperfied connect.home/business (Modbus TCP)", category=WALLBOX,
        description="Amperfied Wallbox connect.home, connect.business, connect.solar über Modbus TCP.",
        capabilities=CAPS,
        notes="Modbus TCP in der Web-Oberfläche aktivieren. Steuerung durch externes Energiemanagement zulassen.",
        fields=[*modbus_fields(unit=255), MAX_CURRENT_FIELD],
    )

    async def connect(self) -> None:
        await ModbusWallbox.connect(self)  # kein Standby-Register bei Amperfied


# ---------------------------------------------------------------- Mennekes Amtron Compact

@register
class MennekesCompact(ModbusWallbox):
    default_framer = "rtu"
    meta = DriverMeta(
        id="mennekes_compact", name="Mennekes Amtron Compact 2.0s (Modbus RTU)", category=WALLBOX,
        description="Mennekes Amtron Compact 2.0s über RS485 und ein Modbus-TCP-Gateway.",
        capabilities=CAPS,
        notes="RS485: 57600 Baud, 8N2, Bus-Adresse 50. Gateway auf Modbus RTU über TCP stellen.",
        fields=[*modbus_fields(unit=50, gateway=True), MAX_CURRENT_FIELD],
    )
    heartbeat_s = 4.0

    async def heartbeat(self) -> None:
        await self.conn.write_register(0x0D00, 0x55AA)

    async def read_data(self) -> WallboxData:
        code = (await self.conn.read_holding(0x0100, 1))[0]
        state = {1: WallboxState.IDLE, 2: WallboxState.CONNECTED, 3: WallboxState.CONNECTED,
                 4: WallboxState.CONNECTED, 5: WallboxState.CHARGING}.get(code, WallboxState.ERROR)
        released = (await self.conn.read_holding(0x0D05, 1))[0] == 1
        limit = f32(await self.conn.read_holding(0x0302, 2))
        power = f32(await self.conn.read_holding(0x0512, 2))
        currents = _f32s(await self.conn.read_holding(0x0500, 6))
        session = f32(await self.conn.read_holding(0x0B02, 2))
        return wallbox_data(state, power, current_set=limit if released else 0.0, currents=currents,
                            energy_session_kwh=session, source="Amtron 0x0512")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self.conn.write_registers(0x0302, _float_regs(self.clamp(amps)))

    async def start_charging(self) -> None:
        await self.conn.write_register(0x0D05, 1)

    async def stop_charging(self) -> None:
        await self.conn.write_register(0x0D05, 0)


# ---------------------------------------------------------------- ABL eMH1

@register
class AblEmh1(LimitWallbox):
    default_framer = "ascii"
    meta = DriverMeta(
        id="abl_emh1", name="ABL eMH1 (Modbus ASCII)", category=WALLBOX,
        description="ABL eMH1 über RS485 (Modbus ASCII) und ein TCP-Gateway.",
        capabilities=CAPS,
        notes="RS485: 38400 Baud, 8E1, Modbus ASCII. Gateway muss ASCII über TCP durchreichen.",
        fields=[*modbus_fields(unit=1, gateway=False),
                ConfigField(key="framer", label="Gateway-Protokoll", type=FieldType.SELECT, default="ascii",
                            options=[{"value": "ascii", "label": "Modbus ASCII über TCP"},
                                     {"value": "socket", "label": "Modbus TCP (Gateway setzt um)"}]),
                MAX_CURRENT_FIELD, PHASES_FIELD],
    )
    DISABLED = 0x03E8

    async def _read(self, address: int, count: int) -> list[int]:
        # Die eMH1 verwirft die erste Anfrage nach einer Pause: zweimal fragen
        try:
            await self.conn.read_holding(address, count)
        except DriverError:
            pass
        return await self.conn.read_holding(address, count)

    async def write_limit(self, amps: float) -> None:
        value = self.DISABLED if amps <= 0 else int(amps / 0.06)
        for _ in range(2):
            await self.conn.write_registers(0x14, [value])

    async def read_data(self) -> WallboxData:
        raw = (await self._read(0x04, 1))[0] & 0xFF
        if raw == 0xE0:
            # Ausgang gesperrt: freischalten, danach regelt der Ladestrom
            await self.conn.write_registers(0x05, [0xA1A1])
            state = WallboxState.CONNECTED
        else:
            state = iec_state(chr(((raw >> 4) - 0x0A) + ord("A")) if raw >= 0xA0 else "")
        cfg = await self._read(0x0F, 5)
        duty = cfg[3] & 0x0FFF
        limit = 0.0 if duty == self.DISABLED else round(duty * 0.06, 1)
        long = await self._read(0x2E, 5)
        currents = [0.0 if v in (self.DISABLED, 1) else v / 10.0 for v in (long[4], long[3], long[2])]
        return wallbox_data(state, sum(currents) * 230.0, current_set=limit, currents=currents,
                            source="ABL 0x2E (230 V angenommen)")


# ---------------------------------------------------------------- em2go

@register
class Em2Go(ModbusWallbox):
    meta = DriverMeta(
        id="em2go", name="EM2GO Home (Modbus TCP)", category=WALLBOX,
        description="EM2GO Home Series über Modbus TCP.",
        capabilities=CAPS,
        fields=[*modbus_fields(unit=255), MAX_CURRENT_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._amps = self.min_current

    async def read_data(self) -> WallboxData:
        code = (await self.conn.read_holding(0, 1))[0]
        state = {1: WallboxState.IDLE, 2: WallboxState.CONNECTED, 3: WallboxState.CONNECTED,
                 4: WallboxState.CHARGING, 6: WallboxState.CONNECTED}.get(code, WallboxState.ERROR)
        enabled = (await self.conn.read_holding(95, 1))[0] == 1
        power = u32(await self.conn.read_holding(12, 2), word_order="big")
        currents = [(await self.conn.read_holding(reg, 1))[0] / 10.0 for reg in (6, 8, 10)]
        return wallbox_data(state, power, current_set=self._amps if enabled else 0.0, currents=currents,
                            source="EM2GO 12")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        self._amps = self.clamp(amps)
        await self.conn.write_registers(91, [int(round(self._amps * 10))])

    async def start_charging(self) -> None:
        await self.conn.write_registers(95, [1])

    async def stop_charging(self) -> None:
        await self.conn.write_registers(95, [2])


# ---------------------------------------------------------------- innogy / E.ON eBox

@register
class InnogyEbox(LimitWallbox):
    meta = DriverMeta(
        id="innogy_ebox", name="innogy / E.ON eBox (Modbus TCP)", category=WALLBOX,
        description="innogy eBox smart/professional (heute E.ON Drive) über Modbus TCP.",
        capabilities=CAPS,
        notes="Modbus TCP in der eBox aktivieren (Konfiguration → Lastmanagement → Modbus).",
        fields=[*modbus_fields(unit=1), MAX_CURRENT_FIELD],
    )

    async def write_limit(self, amps: float) -> None:
        for reg in (1012, 1014, 1016):
            await self.conn.write_registers(reg, _float_regs(amps))

    async def read_data(self) -> WallboxData:
        text = regs_text(await self.conn.read_input(275, 2))
        state = WallboxState.IDLE if text[:1].upper() == "E" else iec_state(text)
        limit = f32(await self.conn.read_holding(1012, 2))
        currents = _f32s(await self.conn.read_input(1006, 6))
        try:
            volts = _f32s(await self.conn.read_input(301, 6))
        except DriverError:
            volts = [230.0, 230.0, 230.0]
        power = sum(u * i for u, i in zip(volts, currents)) if state == WallboxState.CHARGING else 0.0
        return wallbox_data(state, power, current_set=limit if limit >= 6 else 0.0, currents=currents,
                            voltage=volts[0], source="eBox 1006")


# ---------------------------------------------------------------- Siemens VersiCharge

@register
class VersiCharge(LimitWallbox):
    meta = DriverMeta(
        id="versicharge", name="Siemens VersiCharge (Modbus TCP)", category=WALLBOX,
        description="Siemens VersiCharge AC ab Firmware 2.128 über Modbus TCP.",
        capabilities=CAPS,
        fields=[*modbus_fields(unit=2), MAX_CURRENT_FIELD],
    )

    async def write_limit(self, amps: float) -> None:
        await self.conn.write_register(1633, int(round(amps * 100)))

    async def read_data(self) -> WallboxData:
        state = iec_state(regs_text(await self.conn.read_holding(1599, 1)))
        limit = (await self.conn.read_holding(1633, 1))[0] / 100.0
        powers = await self.conn.read_holding(1662, 3)
        power = sum(s16(powers, i) for i in range(3) if powers[i] != 0xFFFF)
        currents = [float(v) for v in await self.conn.read_holding(1647, 3)]
        return wallbox_data(state, power, current_set=limit, currents=currents, source="VersiCharge 1662")


# ---------------------------------------------------------------- Solax EVC

@register
class SolaxEvc(ModbusWallbox):
    meta = DriverMeta(
        id="solax_evc", name="SolaX X1/X3-EVC (Modbus TCP)", category=WALLBOX,
        description="SolaX X1-EVC und X3-EVC über Modbus TCP.",
        capabilities=CAPS,
        notes="Lademodus in der SolaX-App auf 'Fast' stellen, sonst regelt die Box selbst.",
        fields=[*modbus_fields(unit=1), MAX_CURRENT_FIELD],
    )
    STATES = {0: WallboxState.IDLE, 5: WallboxState.IDLE, 2: WallboxState.CHARGING}

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._amps = self.min_current
        self._enabled = False

    async def read_data(self) -> WallboxData:
        code = (await self.conn.read_input(0x1D, 1))[0]
        state = self.STATES.get(code, WallboxState.CONNECTED if code in (1, 3, 7, 8, 11, 12, 13, 17)
                                else WallboxState.ERROR)
        power = float((await self.conn.read_input(0x0B, 1))[0])
        currents = [v / 100.0 for v in await self.conn.read_input(0x04, 3)]
        voltage = (await self.conn.read_input(0x00, 1))[0] / 100.0
        return wallbox_data(state, power, current_set=self._amps if self._enabled else 0.0, currents=currents,
                            voltage=voltage, source="SolaX 0x0B")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        self._amps = self.clamp(amps)
        await self.conn.write_register(0x0628, int(round(self._amps * 100)))

    async def start_charging(self) -> None:
        await self.conn.write_register(0x0627, 4)
        self._enabled = True

    async def stop_charging(self) -> None:
        await self.conn.write_register(0x0627, 3)
        self._enabled = False


# ---------------------------------------------------------------- Peblar

@register
class Peblar(LimitWallbox):
    meta = DriverMeta(
        id="peblar", name="Peblar Home/Business (Modbus TCP)", category=WALLBOX,
        description="Peblar Home, Home Plus und Business über Modbus TCP.",
        capabilities=CAPS,
        notes="Modbus in der Peblar-Oberfläche aktivieren, Strombegrenzung über Modbus zulassen.",
        fields=[*modbus_fields(unit=255), MAX_CURRENT_FIELD],
    )

    async def write_limit(self, amps: float) -> None:
        ma = int(round(amps * 1000))
        await self.conn.write_registers(40000, [(ma >> 16) & 0xFFFF, ma & 0xFFFF])

    async def read_data(self) -> WallboxData:
        state = iec_state(chr((await self.conn.read_input(30110, 1))[0] & 0xFF))
        phases = max(1, min(3, (await self.conn.read_input(30092, 1))[0] or 3))
        power = u32(await self.conn.read_input(30014, 2), word_order="big")
        cur = await self.conn.read_input(30022, 2 * phases)
        currents = [u32(cur, 2 * i, "big") / 1000.0 for i in range(phases)]
        limit = u32(await self.conn.read_holding(40000, 2), word_order="big") / 1000.0
        return wallbox_data(state, power, current_set=limit, currents=currents, source="Peblar 30014")


# ---------------------------------------------------------------- Pulsares, KSE, OBO

@register
class Pulsares(LimitWallbox):
    meta = DriverMeta(
        id="pulsares", name="Pulsares (Modbus TCP)", category=WALLBOX,
        description="Pulsares-Wallboxen über Modbus TCP. Ohne Zähler: Leistung aus Strom und Phasen geschätzt.",
        capabilities=CAPS,
        fields=[*modbus_fields(unit=1), MAX_CURRENT_FIELD, PHASES_FIELD],
    )

    async def write_limit(self, amps: float) -> None:
        await self.conn.write_registers(0x5D, [int(round(amps * 1000))])

    async def read_data(self) -> WallboxData:
        connected = (await self.conn.read_holding(0x1B, 1))[0] == 1
        charging = (await self.conn.read_holding(0x1F, 1))[0] in (3, 4)
        state = (WallboxState.CHARGING if charging else WallboxState.CONNECTED) if connected else WallboxState.IDLE
        limit = (await self.conn.read_holding(0x5D, 1))[0] / 1000.0
        limit = limit if limit >= 6 else 0.0
        return wallbox_data(state, _estimate(state, limit, self.phases), current_set=limit,
                            source="Pulsares (geschätzt)")


@register
class KseWallbox(LimitWallbox):
    default_framer = "rtu"
    meta = DriverMeta(
        id="kse_wallbox", name="KSE wallbox (Modbus RTU)", category=WALLBOX,
        description="KSE wallbox über RS485 und ein Modbus-TCP-Gateway.",
        capabilities=CAPS,
        notes="RS485: 9600 Baud, 8E1, Bus-Adresse 100.",
        fields=[*modbus_fields(unit=100, gateway=True), MAX_CURRENT_FIELD],
    )

    async def write_limit(self, amps: float) -> None:
        await self.conn.write_register(0x03, int(round(amps)))

    async def read_data(self) -> WallboxData:
        code = (await self.conn.read_input(0x10, 1))[0]
        state = {0: WallboxState.IDLE, 1: WallboxState.IDLE, 3: WallboxState.IDLE, 4: WallboxState.CONNECTED,
                 5: WallboxState.CHARGING}.get(code, WallboxState.ERROR)
        limit = float((await self.conn.read_holding(0x03, 1))[0])
        power = float((await self.conn.read_input(0x18, 1))[0])
        currents = [v / 1000.0 for v in await self.conn.read_input(0x14, 3)]
        session = (await self.conn.read_input(0x17, 1))[0] / 100.0
        return wallbox_data(state, power, current_set=limit, currents=currents, energy_session_kwh=session,
                            source="KSE 0x18")


@register
class OboWallbox(ModbusWallbox):
    default_framer = "rtu"
    meta = DriverMeta(
        id="obo_wallbox", name="OBO Bettermann Wallbox (Modbus RTU)", category=WALLBOX,
        description="OBO Bettermann Wallbox über RS485 und ein Modbus-TCP-Gateway. Ohne Zähler: Leistung geschätzt.",
        capabilities=CAPS,
        notes="RS485: 19200 Baud, 8E1, Bus-Adresse 101.",
        fields=[*modbus_fields(unit=101, gateway=True), MAX_CURRENT_FIELD, PHASES_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._amps = self.min_current

    async def read_data(self) -> WallboxData:
        code = (await self.conn.read_holding(11, 1))[0]
        state = {0: WallboxState.IDLE, 1: WallboxState.CONNECTED, 2: WallboxState.CHARGING}.get(
            code, WallboxState.ERROR)
        enabled = (await self.conn.read_holding(5, 1))[0] > 0
        limit = float((await self.conn.read_holding(6, 1))[0])
        current = limit if enabled else 0.0
        return wallbox_data(state, _estimate(state, current, self.phases), current_set=current,
                            source="OBO (geschätzt)")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self.conn.write_register(6, int(round(self.clamp(amps))))

    async def start_charging(self) -> None:
        await self.conn.write_register(5, 1)

    async def stop_charging(self) -> None:
        await self.conn.write_register(5, 0)

