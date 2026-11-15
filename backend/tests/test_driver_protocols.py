"""Treiber gegen nachgestellte Geräteantworten.

Jeder Test baut die Antwort eines Geräts so nach, wie die Dokumentation des
Herstellers bzw. die Referenzimplementierung sie beschreibt (Quellen:
docs/DRIVERS.md), und prüft, was der Treiber daraus macht: Adresse,
Datentyp, Skalierung, Vorzeichen. Ohne Hardware, ohne Netzwerk.
"""
import asyncio
import base64

import pytest

from app.drivers.base import WallboxState
from app.drivers.validation import DeviceRejected


class FakeModbus:
    """Modbus-Gegenstelle: Register als dict {Adresse: Wert}. Fehlende
    Adressen werden wie vom Gerät abgelehnt (Illegal Data Address)."""

    def __init__(self, input: dict | None = None, holding: dict | None = None) -> None:
        self.input = dict(input or {})
        self.holding = dict(holding or {})
        self.writes: list[tuple[int, object]] = []
        self.reads: list[tuple[str, int, int]] = []

    def _read(self, kind: str, table: dict, address: int, count: int) -> list[int]:
        self.reads.append((kind, address, count))
        if any(a not in table for a in range(address, address + count)):
            raise DeviceRejected(f"Register {address} abgelehnt – Adresse nicht vorhanden.")
        return [table[a] & 0xFFFF for a in range(address, address + count)]

    async def read_input(self, address, count=1):
        return self._read("input", self.input, address, count)

    async def read_holding(self, address, count=1):
        return self._read("holding", self.holding, address, count)

    async def write_register(self, address, value, **_):
        self.writes.append((address, value))
        self.holding[address] = value

    async def write_registers(self, address, values, **_):
        self.writes.append((address, list(values)))
        for i, v in enumerate(values):
            self.holding[address + i] = v

    async def connect(self):
        pass

    async def close(self):
        pass


def words_le(value: int) -> tuple[int, int]:
    """32 Bit, niederwertiges Wort zuerst (Sungrow)."""
    value &= 0xFFFFFFFF
    return value & 0xFFFF, value >> 16


def words_be(value: int) -> tuple[int, int]:
    """32 Bit, höherwertiges Wort zuerst (SMA, Huawei)."""
    value &= 0xFFFFFFFF
    return value >> 16, value & 0xFFFF


def put(table: dict, address: int, *values: int) -> None:
    for i, v in enumerate(values):
        table[address + i] = v & 0xFFFF


class FakeHttp:
    """HTTP-Gegenstelle: Pfad → Antwort; merkt sich die Aufrufe."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    async def get_json(self, path, params=None):
        self.calls.append((path, params))
        value = self.routes[path]
        return value(params) if callable(value) else value

    async def get_text(self, path, params=None):
        self.calls.append((path, params))
        return str(self.routes.get(path, ""))

    async def connect(self):
        pass

    async def close(self):
        pass


# ================================================================ Sungrow

SH_STATE_CHARGING = 0x0002
SH_STATE_DISCHARGING = 0x0004


@pytest.mark.parametrize("raw, state, expected", [
    # alte Firmware: immer positiv, Richtung im Zustand
    (3000, SH_STATE_CHARGING, 3000.0),
    (3000, SH_STATE_DISCHARGING, -3000.0),
    (40, 0, 40.0),
    (12, 0, 0.0),                       # Rauschen ohne Richtungsbit
    # neue Firmware (ab Herbst 2024): int16 mit Vorzeichen
    (0x10000 - 3000, SH_STATE_DISCHARGING, -3000.0),
    (0x10000 - 3000, 0, -3000.0),
    (2500, SH_STATE_CHARGING, 2500.0),
])
def test_sungrow_battery_power_old_and_new_firmware(raw, state, expected):
    from app.drivers.sungrow.common import battery_power_w

    assert battery_power_w(raw, state) == expected


def sungrow_sh_registers(*, export_w=1200, battery_raw=1500, state=SH_STATE_CHARGING) -> dict:
    reg: dict = {}
    put(reg, 5016, *words_le(4321))             # 5017 DC-Leistung W
    put(reg, 5002, 123, *words_le(99999))       # 5003 Tag, 5004 gesamt (inkl. Entladung)
    put(reg, 5007, 412)                         # 5008 Temperatur 41,2 °C
    put(reg, 13000, state, 87, *words_le(123456))   # 13001 Zustand, 13002 Tag PV, 13003 gesamt PV
    put(reg, 13009, *words_le(export_w))        # 13010 Export
    put(reg, 13021, battery_raw, 655)           # 13022 Leistung, 13023 SoC 65,5 %
    return reg


async def test_sungrow_sh_reads_power_signs_and_yields():
    from app.drivers.sungrow.inverter import SungrowSHInverter

    drv = SungrowSHInverter({"host": "192.0.2.10"})
    drv.conn = FakeModbus(input=sungrow_sh_registers())
    d = await drv.read_data()
    assert d.pv_power == 4321.0
    assert d.grid_power == -1200.0            # Export positiv → Einspeisung negativ
    assert d.battery_power == 1500.0 and d.battery_soc == 65.5
    assert d.daily_yield_kwh == pytest.approx(8.7)        # 13002, 0,1 kWh
    assert d.total_yield_kwh == pytest.approx(12345.6)    # 13003, 0,1 kWh – nicht 5004

    drv.conn = FakeModbus(input=sungrow_sh_registers(export_w=-800, battery_raw=0x10000 - 2000,
                                                     state=SH_STATE_DISCHARGING))
    d = await drv.read_data()
    assert d.grid_power == 800.0 and d.battery_power == -2000.0


async def test_sungrow_sh_reports_missing_meter_instead_of_a_number():
    from app.drivers.sungrow.inverter import SungrowSHInverter

    reg = sungrow_sh_registers()
    put(reg, 13009, *words_le(0x7FFFFFFF))
    drv = SungrowSHInverter({"host": "192.0.2.10"})
    drv.conn = FakeModbus(input=reg)
    with pytest.raises(DeviceRejected, match="Energiezähler"):
        await drv.read_data()


async def test_sungrow_meter_sign_differs_between_sh_and_sg():
    from app.drivers.sungrow.meter import SungrowMeter

    # SH: 13010 zählt Einspeisung positiv
    sh = SungrowMeter({"host": "192.0.2.10", "series": "sh"})
    sh.conn = FakeModbus(input={13009: words_le(1500)[0], 13010: words_le(1500)[1]})
    assert (await sh.read_data()).grid_power == -1500.0
    # SG: 5083 zählt Bezug positiv
    sg = SungrowMeter({"host": "192.0.2.10", "series": "sg"})
    sg.conn = FakeModbus(input={5082: words_le(1500)[0], 5083: words_le(1500)[1]})
    assert (await sg.read_data()).grid_power == 1500.0
    lo, hi = words_le(-700)
    sg.conn = FakeModbus(input={5082: lo, 5083: hi})
    assert (await sg.read_data()).grid_power == -700.0


async def test_sungrow_wallbox_register_map():
    from app.drivers.sungrow.wallbox import SungrowWallbox

    reg: dict = {21316: 3, 21269: 0, 21301: 2315}
    put(reg, 21307, *words_le(11040), *words_le(7350))
    conn = FakeModbus(input=reg, holding={21202: 160})
    drv = SungrowWallbox({"host": "192.0.2.11", "max_current": 16, "phase_switch": True})
    drv.conn = conn
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 11040.0
    assert d.energy_session_kwh == pytest.approx(7.35) and d.current_set == 16.0
    assert d.phases_active == 3 and d.voltage == pytest.approx(231.5)

    await drv.set_current(10.4)
    await drv.stop_charging()
    await drv.start_charging()
    await drv.set_phases(1)
    assert conn.writes == [(21202, 104), (21211, 1), (21210, 1), (21211, 0), (21203, 1)]
    await drv.set_current(40)          # über der Grenze der Box
    assert conn.writes[-1] == (21202, 160)

    for raw, state in ((1, WallboxState.IDLE), (2, WallboxState.CONNECTED), (6, WallboxState.COMPLETE),
                       (9, WallboxState.ERROR)):
        conn.input[21316] = raw
        assert (await drv.read_data()).state == state


# ================================================================ my-PV

@pytest.mark.parametrize("payload, power", [
    ({"device": "AC ELWA 2", "power_elwa2": 1840, "temp1": 512, "status": 2}, 1840.0),
    ({"device": "AC•THOR", "power_act": 2100, "temp1": 480, "status": 9}, 2100.0),
    ({"device": "AC•THOR 9s", "power_act": 2100, "power_ac9": 6000, "temp1": 480, "status": "9, 9"}, 8100.0),
    ({"device": "AC ELWA-E", "power": 900, "temp1": 455, "status": 2}, 900.0),
])
async def test_mypv_http_power_key_per_device_family(payload, power):
    from app.drivers.mypv import MyPvAcThor

    drv = MyPvAcThor({"host": "192.0.2.20"})
    drv.http = FakeHttp({"/data.jsn": payload})
    d = await drv.read_data()
    assert d.power == power and d.is_on
    assert d.temperature_c == pytest.approx(payload["temp1"] / 10)
    assert "Fehler" not in str(d.extra)      # Status 9 ist kein Fehler


async def test_mypv_modbus_without_register_1074_and_error_status():
    from app.drivers.mypv import MyPvAcThor

    drv = MyPvAcThor({"host": "192.0.2.20", "control_method": "modbus_tcp"})
    # AC ELWA-E: Register 1074 gibt es nicht
    conn = FakeModbus(holding={1000: 1500, 1001: 503, 1002: 600, 1003: 2})
    drv._conn = conn
    d = await drv.read_data()
    assert d.power == 1500.0 and d.is_on and d.temperature_c == pytest.approx(50.3)
    await drv.read_data()
    assert [r for r in conn.reads if r[1] == 1074] == [("holding", 1074, 1)]   # nur einmal versucht

    conn.holding.update({1000: 0, 1003: 201})
    d = await drv.read_data()
    assert not d.is_on and "Übertemperatur" in d.extra["Gerätestatus"]

    await drv.set_power(99999)
    assert conn.writes[-1] == (1000, 3000)     # auf die Nennleistung begrenzt
    conn.holding[1003] = 1
    await drv.read_data()
    with pytest.raises(DeviceRejected, match="No Control"):
        await drv.set_power(500)


# ================================================================ go-e

def goe_status(**over) -> dict:
    nrg = [231, 232, 230, 2, 16.0, 15.9, 16.1, 3690, 3680, 3700, 0, 11070, 99, 99, 99, 0]
    return {"car": 2, "alw": True, "amp": 16, "nrg": nrg, "wh": 4200, "psm": 2, "frc": 2, "trx": None, "err": 0,
            **over}


async def test_goe_reads_power_energy_and_real_phases():
    from app.drivers.goe import GoeCharger

    drv = GoeCharger({"host": "192.0.2.30"})
    drv.http = FakeHttp({"/api/status": goe_status()})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 11070.0 and d.phases_active == 3
    assert d.energy_session_kwh == pytest.approx(4.2) and d.current_set == 16.0

    # einphasiges Fahrzeug an einer Box im Modus 'dreiphasig'
    single = goe_status(nrg=[231, 232, 230, 2, 16.0, 0.0, 0.1, 3690, 0, 0, 0, 3690, 99, 0, 0, 0])
    drv.http = FakeHttp({"/api/status": single})
    assert (await drv.read_data()).phases_active == 1
    # ohne Last: Vorgabe; bei 'Auto' unbekannt statt geraten
    idle = goe_status(car=3, nrg=[231, 232, 230, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], psm=0)
    drv.http = FakeHttp({"/api/status": idle})
    d = await drv.read_data()
    assert d.phases_active is None and d.state == WallboxState.CONNECTED
    drv.http = FakeHttp({"/api/status": goe_status(car=5)})
    assert (await drv.read_data()).state == WallboxState.ERROR


async def test_goe_start_forces_on_and_stop_forces_off():
    from app.drivers.goe import GoeCharger

    state = {"frc": 0}

    def setter(params):
        state.update({k: int(v) for k, v in params.items()})
        return {}

    http = FakeHttp({"/api/set": setter, "/api/status": lambda params: dict(state)})
    drv = GoeCharger({"host": "192.0.2.30"})
    drv.http = http
    await drv.start_charging()
    assert state["frc"] == 2          # 2 = ein, nicht 0 = neutral
    await drv.stop_charging()
    assert state["frc"] == 1


# ================================================================ Fronius

def fronius_flow(site: dict, inverters: dict | None = None) -> dict:
    return {"Body": {"Data": {"Site": site, "Inverters": inverters or {}}}}


async def test_fronius_soc_comes_from_inverters_and_signs_follow_the_api():
    from app.drivers.fronius import POWERFLOW, FroniusInverter, FroniusMeter

    flow = fronius_flow({"P_PV": 5200.5, "P_Grid": -1800.0, "P_Akku": -900.0, "E_Day": 12500, "E_Total": 9_876_000,
                         "Mode": "bidirectional"}, {"1": {"SOC": 73.4, "P": 4300}})
    drv = FroniusInverter({"host": "192.0.2.40"})
    drv.http = FakeHttp({POWERFLOW: flow})
    d = await drv.read_data()
    assert d.pv_power == 5200.5 and d.grid_power == -1800.0   # P_Grid: + Bezug, − Einspeisung
    assert d.battery_power == 900.0                           # P_Akku: − = laden → bei uns +
    assert d.battery_soc == 73.4
    assert d.daily_yield_kwh == 12.5 and d.total_yield_kwh == 9876.0

    meter = FroniusMeter({"host": "192.0.2.40"})
    meter.http = FakeHttp({POWERFLOW: flow})
    assert (await meter.read_data()).grid_power == -1800.0

    # nachts: P_PV ist null, kein Speicher
    drv.http = FakeHttp({POWERFLOW: fronius_flow({"P_PV": None, "P_Grid": 340.0, "P_Akku": None})})
    d = await drv.read_data()
    assert d.pv_power == 0.0 and d.battery_soc is None and d.battery_power is None


# ================================================================ Huawei

async def test_huawei_battery_power_and_soc_registers():
    from app.drivers.huawei import HuaweiSun2000

    hold: dict = {}
    put(hold, 32064, *words_be(6400))        # DC-Leistung W
    put(hold, 32114, *words_be(1234))        # Tagesertrag 12,34 kWh
    put(hold, 32106, *words_be(987654))      # Gesamtertrag 9876,54 kWh
    put(hold, 37765, *words_be(-2500))       # Speicher: entlädt mit 2,5 kW
    put(hold, 37760, 655)                    # SoC 65,5 %
    drv = HuaweiSun2000({"host": "192.0.2.50"})
    drv.conn = FakeModbus(holding=hold)
    d = await drv.read_data()
    assert d.pv_power == 6400.0 and d.battery_power == -2500.0 and d.battery_soc == 65.5
    assert d.daily_yield_kwh == pytest.approx(12.34) and d.total_yield_kwh == pytest.approx(9876.54)

    # ohne Speicher: keine Speicherwerte, kein erfundener Ladestand
    for address in (37765, 37766, 37760):
        hold.pop(address)
    drv.conn = FakeModbus(holding=hold)
    d = await drv.read_data()
    assert d.battery_power is None and d.battery_soc is None


async def test_huawei_meter_sign_is_export_positive():
    from app.drivers.huawei import HuaweiPowerSensor

    hold: dict = {}
    put(hold, 37113, *words_be(1800))        # Huawei: > 0 = Einspeisung
    drv = HuaweiPowerSensor({"host": "192.0.2.50"})
    drv.conn = FakeModbus(holding=hold)
    assert (await drv.read_data()).grid_power == -1800.0


# ================================================================ SMA

SMA_NAN_S32 = 0x80000000
SMA_NAN_U32 = 0xFFFFFFFF


def sma_registers(ac_w, *, soc=None, charge=None, discharge=None, dc_a=None, dc_b=None) -> dict:
    hold: dict = {}
    put(hold, 30775, *words_be(ac_w))
    put(hold, 30535, *words_be(15300))
    put(hold, 30513, 0, 0, *words_be(4_500_000))
    put(hold, 30845, *words_be(SMA_NAN_U32 if soc is None else soc))
    put(hold, 31393, *words_be(SMA_NAN_U32 if charge is None else charge))
    put(hold, 31395, *words_be(SMA_NAN_U32 if discharge is None else discharge))
    put(hold, 30773, *words_be(SMA_NAN_S32 if dc_a is None else dc_a))
    put(hold, 30961, *words_be(SMA_NAN_S32 if dc_b is None else dc_b))
    return hold


async def test_sma_night_marker_is_zero_and_hybrid_uses_dc_power():
    from app.drivers.sma import SmaInverter

    drv = SmaInverter({"host": "192.0.2.60"})
    drv.conn = FakeModbus(holding=sma_registers(SMA_NAN_S32))
    d = await drv.read_data()
    assert d.pv_power == 0.0 and d.battery_soc is None and d.battery_power is None
    assert d.daily_yield_kwh == 15.3 and d.total_yield_kwh == 4500.0

    # reiner PV-Wechselrichter: AC-Leistung
    drv.conn = FakeModbus(holding=sma_registers(4200, dc_a=2200, dc_b=2150))
    assert (await drv.read_data()).pv_power == 4200.0

    # Hybrid nachts: Speicher entlädt 1,8 kW, die Sonne liefert nichts
    drv.conn = FakeModbus(holding=sma_registers(1750, soc=62, charge=0, discharge=1800, dc_a=0, dc_b=0))
    d = await drv.read_data()
    assert d.pv_power == 0.0 and d.battery_power == -1800.0 and d.battery_soc == 62.0


async def test_sma_meter_import_minus_export():
    from app.drivers.sma import SmaMeter

    hold: dict = {}
    put(hold, 30865, *words_be(0))
    put(hold, 30867, *words_be(2300))
    drv = SmaMeter({"host": "192.0.2.60"})
    drv.conn = FakeModbus(holding=hold)
    assert (await drv.read_data()).grid_power == -2300.0


# ================================================================ Victron

async def test_victron_system_registers():
    from app.drivers.victron import VictronGx

    hold: dict = {}
    put(hold, 808, 500, 0, 0, 1200, 0, 0)        # PV am AC-Ausgang und -Eingang
    put(hold, 820, 0x10000 - 300, 150, 0x10000 - 50)   # Netz L1–L3 (int16)
    put(hold, 842, 0x10000 - 1500, 81)           # Batterie: entlädt, SoC 81 %
    put(hold, 850, 2400)                         # PV am DC-Bus
    drv = VictronGx({"host": "192.0.2.70"})
    drv.conn = FakeModbus(holding=hold)
    d = await drv.read_data()
    assert d.pv_power == 4100.0 and d.grid_power == -200.0
    assert d.battery_power == -1500.0 and d.battery_soc == 81.0


# ================================================================ Shelly

async def test_shelly_generations():
    from app.drivers.shelly import Shelly3EM

    gen2 = Shelly3EM({"host": "192.0.2.80", "generation": "gen2"})
    gen2.http = FakeHttp({"/rpc/EM.GetStatus": {"total_act_power": -1234.5, "a_act_power": -400.0,
                                                "b_act_power": -434.5, "c_act_power": -400.0}})
    assert (await gen2.read_data()).grid_power == -1234.5
    gen1 = Shelly3EM({"host": "192.0.2.80", "generation": "gen1"})
    gen1.http = FakeHttp({"/status": {"emeters": [{"power": 100.5}, {"power": 200.0}, {"power": -50.5}]}})
    assert (await gen1.read_data()).grid_power == 250.0


# ================================================================ Tesla

class RecordingSession:
    def __init__(self) -> None:
        self.posts: list[str] = []

    def post(self, url, **_):
        self.posts.append(url)
        return _Response()

    closed = False


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    async def json(self, content_type=None):
        return {"response": {"result": True}}


async def test_tesla_wake_up_path_depends_on_the_backend():
    from app.drivers.tesla.clients import make_client

    proxy = make_client({"backend": "ble_proxy", "proxy_url": "192.0.2.90", "proxy_port": 8080,
                         "vin": "XP7TEST0000000001"})
    proxy._session = RecordingSession()
    await proxy.wake_up()
    assert proxy._session.posts == ["http://192.0.2.90:8080/api/1/vehicles/XP7TEST0000000001/command/wake_up"]

    fleet = make_client({"backend": "fleet", "vehicle_id": "12345", "access_token": "t", "region": "eu"})
    fleet._session = RecordingSession()
    await fleet.wake_up()
    assert fleet._session.posts == ["https://fleet-api.prd.eu.vn.cloud.tesla.com/api/1/vehicles/12345/wake_up"]


# ================================================================ OCPP

def basic(user: str, password: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_ocpp_handshake_accepts_only_configured_charge_points():
    from app.drivers.ocpp_driver import OcppCentralServer

    server = OcppCentralServer()
    server.expect("garage")
    server.expect("carport", "geheim")
    assert server.check_handshake("/garage", {}) is None
    assert server.check_handshake("/ocpp/garage", {}) is None          # Pfadpräfix der Box
    assert server.check_handshake("/fremd", {})[0] == 404
    assert server.check_handshake("/", {})[0] == 404
    assert server.check_handshake("/carport", {})[0] == 401
    assert server.check_handshake("/carport", basic("carport", "falsch"))[0] == 401
    assert server.check_handshake("/carport", basic("garage", "geheim"))[0] == 401
    assert server.check_handshake("/carport", basic("carport", "geheim")) is None


def test_ocpp_meter_values_sum_phases_and_keep_totals():
    from app.drivers.ocpp_driver import ChargePointSession, OcppCentralServer

    server = OcppCentralServer()
    s = ChargePointSession("garage")

    def sample(value, measurand, **extra):
        return {"value": str(value), "measurand": measurand, **extra}

    server._parse_meter_values(s, {"meterValue": [{"sampledValue": [
        sample(3680, "Power.Active.Import", phase="L1", unit="W"),
        sample(3690, "Power.Active.Import", phase="L2", unit="W"),
        sample(3700, "Power.Active.Import", phase="L3-N", unit="W"),
        sample(15, "Current.Offered", unit="A"),
        sample(1234567, "Energy.Active.Import.Register", unit="Wh"),
    ]}]})
    assert s.power == 11070.0 and s.current_offered == 15.0 and s.energy_kwh == pytest.approx(1234.567)

    server._parse_meter_values(s, {"meterValue": [{"sampledValue": [
        sample(7.4, "Power.Active.Import", unit="kW"),
        sample(2500, "Power.Active.Import", phase="L1", unit="W"),
        sample(1240.5, "Energy.Active.Import.Register", unit="kWh"),
    ]}]})
    assert s.power == 7400.0 and s.energy_kwh == pytest.approx(1240.5)   # Gesamtwert gewinnt


async def test_ocpp_server_end_to_end_with_password():
    """Echte WebSocket-Verbindung auf localhost: Anmeldung, Start, Messwerte,
    Ladeprofil mit Startzeit."""
    import json
    import socket

    import websockets

    from app.drivers.ocpp_driver import OcppCentralServer, OcppWallbox

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    OcppCentralServer._instance = None
    drv = OcppWallbox({"charge_point_id": "garage", "port": port, "password": "geheim", "max_current": 16})
    await drv.connect()
    url = f"ws://127.0.0.1:{port}/garage"
    try:
        with pytest.raises(Exception):
            async with websockets.connect(url, subprotocols=["ocpp1.6"]):
                pass
        with pytest.raises(Exception):
            async with websockets.connect(f"ws://127.0.0.1:{port}/fremd", subprotocols=["ocpp1.6"]):
                pass
        auth = {"Authorization": basic("garage", "geheim")["Authorization"]}
        async with websockets.connect(url, subprotocols=["ocpp1.6"], extra_headers=auth) as ws:
            async def call(action, payload):
                await ws.send(json.dumps([2, action, action, payload]))
                return json.loads(await asyncio.wait_for(ws.recv(), 5))[2]

            assert (await call("BootNotification", {"chargePointVendor": "T", "chargePointModel": "X"}))["status"] == "Accepted"
            await call("StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"})
            tx = await call("StartTransaction", {"connectorId": 1, "idTag": "a", "meterStart": 1000, "timestamp": "t"})
            await call("MeterValues", {"connectorId": 1, "transactionId": tx["transactionId"], "meterValue": [
                {"sampledValue": [{"value": "7360", "measurand": "Power.Active.Import", "unit": "W"},
                                  {"value": "3500", "measurand": "Energy.Active.Import.Register", "unit": "Wh"}]}]})
            d = await drv.read_data()
            assert d.state == WallboxState.CHARGING and d.power == 7360.0
            assert d.energy_session_kwh == pytest.approx(2.5)

            async def box():
                msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                await ws.send(json.dumps([3, msg[1], {"status": "Accepted"}]))
                return msg

            answer = asyncio.create_task(box())
            await drv.set_current(10)
            msg = await answer
            schedule = msg[3]["csChargingProfiles"]["chargingSchedule"]
            assert msg[2] == "SetChargingProfile" and schedule["chargingSchedulePeriod"][0]["limit"] == 10
            assert schedule["startSchedule"].endswith("Z") and schedule["chargingRateUnit"] == "A"
    finally:
        server = OcppCentralServer.instance()
        if server._server is not None:
            server._server.close()
            await server._server.wait_closed()
        OcppCentralServer._instance = None
