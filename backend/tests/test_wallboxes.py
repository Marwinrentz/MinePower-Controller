"""Wallbox-Treiber (3.0): Modbus, HTTP, MQTT, WebSocket und Cloud. Alle
Gegenstellen sind Fakes; geprüft werden Zustandsabbildung, Leistung,
Sollstrom und die gesendeten Befehle."""
import asyncio
import json
import struct

import pytest

from app.drivers import registry
from app.drivers.base import WallboxState
from app.drivers.validation import CommandNotApplied, ConnectionProblem, DriverError
from app.drivers.wallbox_common import iec_state, regs_text
from app.drivers.wattpilot import auth_message, hash_password, secured

registry.discover()


def make(driver_id: str, **config):
    return registry.create_driver(driver_id, config)


def f32(x: float) -> list[int]:
    return list(struct.unpack(">HH", struct.pack(">f", x)))


def u32(x: int) -> list[int]:
    return [(x >> 16) & 0xFFFF, x & 0xFFFF]


def text(s: str, regs: int) -> list[int]:
    raw = s.encode().ljust(regs * 2, b"\x00")
    return [int.from_bytes(raw[i:i + 2], "big") for i in range(0, regs * 2, 2)]


def at(base: int, values: list[int]) -> dict[int, int]:
    return {base + i: v for i, v in enumerate(values)}


class FakeModbus:
    def __init__(self, holding=None, inputs=None, coils=None):
        self.h = dict(holding or {})
        self.i = dict(inputs or {})
        self.c = dict(coils or {})
        self.writes: list = []

    async def read_holding(self, address, count=1):
        return [self.h.get(address + k, 0) for k in range(count)]

    async def read_input(self, address, count=1):
        return [self.i.get(address + k, 0) for k in range(count)]

    async def read_coils(self, address, count=1):
        return [self.c.get(address + k, False) for k in range(count)]

    async def write_register(self, address, value, verify=False, expect=None):
        self.writes.append((address, value))
        self.h[address] = value

    async def write_registers(self, address, values, verify=False):
        self.writes.append((address, list(values)))
        for k, v in enumerate(values):
            self.h[address + k] = v

    async def write_coil(self, address, value):
        self.writes.append((address, bool(value)))
        self.c[address] = bool(value)


def test_iec_state_and_text():
    assert iec_state("A") == WallboxState.IDLE and iec_state("B1") == WallboxState.CONNECTED
    assert iec_state("C2") == WallboxState.CHARGING and iec_state("F") == WallboxState.ERROR
    assert iec_state("") == WallboxState.ERROR
    assert regs_text(text("C2", 5)) == "C2"


def test_all_wallbox_drivers_are_experimental_and_controllable():
    from app.drivers.base import Maturity
    new = {"cfos_powerbrain", "alfen_eve", "webasto_next", "vestel_evc04", "bender_cc", "phoenix_charx",
           "phoenix_ev_eth", "keba_modbus", "heidelberg_ec", "amperfied", "mennekes_compact", "abl_emh1", "em2go",
           "innogy_ebox", "versicharge", "solax_evc", "peblar", "pulsares", "kse_wallbox", "obo_wallbox",
           "hardybarth_ecb1", "hardybarth_salia", "sma_evcharger", "evse_wifi", "openwb_pro", "http_wallbox",
           "openwb_mqtt", "mqtt_wallbox", "fronius_wattpilot", "easee", "zaptec"}
    metas = {m.id: m for m in registry.all_metas()}
    assert new <= set(metas)
    for driver_id in new:
        assert metas[driver_id].maturity == Maturity.EXPERIMENTAL, driver_id
        assert "write_test" in metas[driver_id].capabilities, driver_id


# ---------------------------------------------------------------- Modbus

async def test_cfos():
    drv = make("cfos_powerbrain", host="192.0.2.20")
    drv.conn = FakeModbus(holding={8092: 2, 8093: 160, 8094: 1, **at(8062, u32(7360)),
                                   **at(8064, u32(160) + u32(160) + u32(160))})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 7360 and d.current_set == 16 and d.phases_active == 3
    await drv.set_current(10)
    await drv.stop_charging()
    assert drv.conn.writes == [(8093, 100), (8094, 0)]


async def test_cfos_overtemperature_is_error():
    drv = make("cfos_powerbrain", host="192.0.2.20")
    drv.conn = FakeModbus(holding={8092: 5})
    with pytest.raises(DriverError):
        await drv.read_data()


async def test_alfen_float_registers_and_limit():
    drv = make("alfen_eve", host="192.0.2.20")
    drv.conn = FakeModbus(holding={**at(1201, text("C2", 5)), **at(1210, f32(10.0)), **at(344, f32(6900.0)),
                                   **at(320, f32(10.0) * 3), **at(306, f32(231.0))})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == pytest.approx(6900) and d.current_set == 10
    assert d.voltage == pytest.approx(231)
    await drv.set_current(12)
    assert drv.conn.writes[-1] == (1210, f32(12.0))
    await drv.stop_charging()
    assert drv.conn.writes[-1] == (1210, f32(0.0))
    await drv.heartbeat()  # gesperrt: Auffrischen schreibt weiter 0 A
    assert drv.conn.writes[-1] == (1210, f32(0.0))


async def test_limit_wallbox_write_test_never_starts_charging():
    drv = make("alfen_eve", host="192.0.2.20")
    drv.conn = FakeModbus(holding={**at(1201, text("B1", 5)), **at(1210, f32(0.0))})
    res = await drv.test_command()
    assert res["ok"] and drv.conn.writes == [(1210, f32(0.0))]


async def test_webasto_next_and_heartbeat():
    drv = make("webasto_next", host="192.0.2.20")
    drv.conn = FakeModbus(holding={1000: 3, **at(1020, u32(3680))}, inputs={1008: 16000, 1010: 0, 1012: 0})
    await drv.set_current(16)
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 3680 and d.current_set == 16 and d.phases_active == 1
    await drv.heartbeat()
    await drv.stop_charging()
    assert drv.conn.writes == [(5004, 16), (6000, 1), (5004, 0)]


async def test_vestel_cable_and_charge_status():
    drv = make("vestel_evc04", host="192.0.2.20")
    drv.conn = FakeModbus(inputs={1004: 3, 1001: 0, **at(1502, u32(4200))})
    d = await drv.read_data()
    assert d.state == WallboxState.CONNECTED and d.energy_session_kwh == pytest.approx(4.2)
    drv.conn.i[1004] = 0
    assert (await drv.read_data()).state == WallboxState.IDLE


async def test_bender_reads_soc_and_estimates_power_on_legacy():
    drv = make("bender_cc", host="192.0.2.20")
    drv.conn = FakeModbus(holding={122: 3, **at(212, u32(16000) * 3), **at(220, u32(11040)), 1000: 16, 730: 64})
    d = await drv.read_data()
    assert d.power == 11040 and d.soc == 64 and d.current_set == 16 and d.phases_active == 3
    drv.conn.h.update(at(220, u32(0xFFFFFFFF)))
    assert (await drv.read_data()).power == pytest.approx(3 * 16 * 230)
    await drv.set_current(8)
    assert drv.conn.writes[-1] == (1000, [8])


async def test_phoenix_charx_connector_offsets():
    drv = make("phoenix_charx", host="192.0.2.20", connector=2)
    drv.conn = FakeModbus(holding={2299: ord("C") << 8, 2301: 10, **at(2244, u32(6900000)),
                                   **at(2238, u32(10000) * 3), 2264: 55})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 6900 and d.soc == 55
    await drv.set_current(13)
    assert drv.conn.writes[-1] == (2301, [13])


async def test_phoenix_ev_eth_coil_and_wallbe_scaling():
    drv = make("phoenix_ev_eth", host="192.0.2.20")
    drv.conn = FakeModbus(inputs={100: ord("B"), **at(120, [0, 0])}, holding={528: 160}, coils={400: True})
    d = await drv.read_data()
    assert d.state == WallboxState.CONNECTED and d.current_set == 16  # 160 × 0,1 A
    await drv.set_current(10)
    await drv.stop_charging()
    assert drv.conn.writes == [(528, 100), (400, False)]


async def test_keba_modbus_p30_and_p40():
    drv = make("keba_modbus", host="192.0.2.20")
    drv.conn = FakeModbus(holding={**at(1000, u32(3)), **at(1004, u32(7)), **at(1020, u32(7200000)),
                                   **at(1016, u32(311110))})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 7200
    await drv.set_current(10)
    await drv.stop_charging()
    assert drv.conn.writes == [(5004, 10000), (5014, 0)]
    p40 = make("keba_modbus", host="192.0.2.20")
    p40.conn = FakeModbus(holding={**at(1016, u32(4111100))})
    await p40.stop_charging()
    assert p40.conn.writes == [(5004, 0)]


async def test_heidelberg_states_and_remote_lock():
    drv = make("heidelberg_ec", host="192.0.2.20")
    assert drv.conn.framer == "rtu"
    drv.conn = FakeModbus(inputs={5: 7, 6: 160, 7: 160, 8: 160, 10: 230, 14: 11000}, holding={261: 160})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.current_set == 16 and d.power == 11000
    drv.conn.i[5] = 10
    assert (await drv.read_data()).state == WallboxState.ERROR
    assert (259, [1]) in drv.conn.writes
    await drv.set_current(6.5)
    assert drv.conn.writes[-1] == (261, [65])


async def test_mennekes_compact_release_and_heartbeat():
    drv = make("mennekes_compact", host="192.0.2.20")
    drv.conn = FakeModbus(holding={0x0100: 5, 0x0D05: 1, **at(0x0302, f32(12.0)), **at(0x0512, f32(2760.0))})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.current_set == 12 and d.power == pytest.approx(2760)
    await drv.heartbeat()
    await drv.stop_charging()
    assert drv.conn.writes == [(0x0D00, 0x55AA), (0x0D05, 0)]


async def test_abl_status_byte_and_duty_cycle():
    drv = make("abl_emh1", host="192.0.2.20")
    drv.conn = FakeModbus(holding={0x04: 0xC2, **at(0x0F, [0, 0, 0, 266, 0]), **at(0x2E, [0, 0, 160, 160, 160])})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.current_set == pytest.approx(16.0, abs=0.1)
    assert d.power == pytest.approx(3 * 16 * 230)
    await drv.stop_charging()
    assert drv.conn.writes[-2:] == [(0x14, [0x03E8]), (0x14, [0x03E8])]
    drv.conn.h[0x04] = 0xE0
    assert (await drv.read_data()).state == WallboxState.CONNECTED
    assert (0x05, [0xA1A1]) in drv.conn.writes


async def test_em2go_versicharge_solax_peblar():
    em = make("em2go", host="192.0.2.20")
    em.conn = FakeModbus(holding={0: 4, 95: 1, **at(12, u32(3600)), 6: 160})
    assert (await em.read_data()).power == 3600
    await em.set_current(10)
    await em.stop_charging()
    assert em.conn.writes == [(91, [100]), (95, [2])]

    vc = make("versicharge", host="192.0.2.20")
    vc.conn = FakeModbus(holding={**at(1599, text("C", 1)), 1633: 1600, **at(1662, [1200, 1200, 0xFFFF])})
    d = await vc.read_data()
    assert d.state == WallboxState.CHARGING and d.current_set == 16 and d.power == 2400

    sx = make("solax_evc", host="192.0.2.20")
    sx.conn = FakeModbus(inputs={0x1D: 2, 0x0B: 4000})
    assert (await sx.read_data()).state == WallboxState.CHARGING
    await sx.start_charging()
    await sx.set_current(7)
    assert sx.conn.writes == [(0x0627, 4), (0x0628, 700)]

    pb = make("peblar", host="192.0.2.20")
    pb.conn = FakeModbus(inputs={30110: ord("B"), 30092: 1, **at(30014, u32(0))}, holding=at(40000, u32(16000)))
    d = await pb.read_data()
    assert d.state == WallboxState.CONNECTED and d.current_set == 16
    await pb.stop_charging()
    assert pb.conn.writes == [(40000, [0, 0])]


async def test_drivers_without_meter_estimate_power():
    pu = make("pulsares", host="192.0.2.20", phases="1")
    pu.conn = FakeModbus(holding={0x1B: 1, 0x1F: 3, 0x5D: 10000})
    d = await pu.read_data()
    assert d.state == WallboxState.CHARGING and d.power == pytest.approx(2300)
    obo = make("obo_wallbox", host="192.0.2.20", phases="3")
    obo.conn = FakeModbus(holding={11: 2, 5: 1, 6: 16})
    assert (await obo.read_data()).power == pytest.approx(16 * 3 * 230)


async def test_kse_and_innogy():
    kse = make("kse_wallbox", host="192.0.2.20")
    kse.conn = FakeModbus(inputs={0x10: 5, 0x18: 3600, 0x17: 250, **at(0x14, [16000, 0, 0])}, holding={0x03: 16})
    d = await kse.read_data()
    assert d.state == WallboxState.CHARGING and d.energy_session_kwh == 2.5 and d.phases_active == 1
    eb = make("innogy_ebox", host="192.0.2.20")
    eb.conn = FakeModbus(inputs={**at(275, text("C1", 2)), **at(1006, f32(10.0) * 3), **at(301, f32(230.0) * 3)},
                         holding=at(1012, f32(10.0)))
    d = await eb.read_data()
    assert d.state == WallboxState.CHARGING and d.power == pytest.approx(6900)
    await eb.stop_charging()
    assert [w[0] for w in eb.conn.writes] == [1012, 1014, 1016]


# ---------------------------------------------------------------- HTTP

class FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list = []

    async def get_json(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self.responses(path) if callable(self.responses) else self.responses[path]

    async def get_text(self, path, params=None):
        self.calls.append(("GET", path, params))
        value = self.responses(path) if callable(self.responses) else self.responses.get(path, "S0_ok")
        return value if isinstance(value, str) else json.dumps(value)

    async def request(self, method, path, *, params=None, json=None, data=None, headers=None, want="json"):
        self.calls.append((method, path, json if json is not None else data))
        if callable(self.responses):
            return self.responses(path)
        return self.responses.get(path)


async def test_hardybarth_ecb1():
    drv = make("hardybarth_ecb1", host="192.0.2.21")
    drv.http = FakeHttp({"/chargecontrols/1": {"chargecontrol": {"State": "charging", "StateID": 5, "Connected": True,
                                                                  "ManualModeAmp": 10, "Mode": "manual"}},
                         "/meters/1": {"meter": {"data": {"1-0:1.4.0": 6900, "1-0:31.4.0": 10, "1-0:51.4.0": 10,
                                                          "1-0:71.4.0": 10}}}})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 6900 and d.current_set == 10
    await drv.set_current(12)
    assert drv.http.calls[-2] == ("POST", "/chargecontrols/1/mode", {"mode": "manual"})
    assert drv.http.calls[-1] == ("POST", "/chargecontrols/1/mode/manual/ampere", {"manualmodeamp": "12"})


async def test_salia_firmware_dependent_endpoint():
    api = {"device": {"software_version": "2.3.70"}, "secc": {"port0": {
        "ci": {"charge": {"cp": {"status": "B"}}, "evse": {"basic": {"offered_current_limit": "16"}}},
        "salia": {"pausecharging": "0", "chargemode": "manual"},
        "metering": {"power": {"active_total": {"actual": "0"}}}}}}
    drv = make("hardybarth_salia", host="192.0.2.22")
    drv.http = FakeHttp(lambda p: api if p == "/api" else {"result": "ok"})
    d = await drv.read_data()
    assert d.state == WallboxState.CONNECTED and d.current_set == 16
    await drv.stop_charging()
    assert drv.http.calls[-1] == ("POST", "/save_mqtt.php", {"salia/pausecharging": "1"})


async def test_sma_evcharger_measurements_and_params():
    def resp(path):
        if path == "/token":
            return {"access_token": "t", "expires_in": 3600}
        if path == "/measurements/live":
            return [{"channelId": "Measurement.Operation.EVeh.ChaStt", "values": [{"value": 200113}]},
                    {"channelId": "Measurement.Metering.GridMs.TotWIn", "values": [{"value": 7400}]},
                    {"channelId": "Measurement.ChaSess.WhIn", "values": [{"value": 1500}]}]
        if path == "/parameters/search/":
            return [{"values": [{"channelId": "Parameter.Chrg.ActChaMod", "value": "4718"},
                                {"channelId": "Parameter.Inverter.AcALim", "value": "10.00"}]}]
        return None

    drv = make("sma_evcharger", host="192.0.2.23", username="u", password="p")
    drv.http = FakeHttp(resp)
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 7400 and d.current_set == 10
    assert d.energy_session_kwh == 1.5
    await drv.stop_charging()
    method, path, body = drv.http.calls[-1]
    assert method == "PUT" and body["values"][0]["value"] == "4721"


async def test_evse_wifi_and_rejection():
    drv = make("evse_wifi", host="192.0.2.24")
    drv.http = FakeHttp({"/getParameters": {"list": [{"vehicleState": 3, "evseState": True, "actualCurrent": 16,
                                                      "actualPower": 3.68, "energy": 2.1}]},
                         "/setStatus": "E2_invalid"})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == pytest.approx(3680) and d.current_set == 16
    with pytest.raises(Exception):
        await drv.stop_charging()


async def test_openwb_pro_and_generic_http():
    drv = make("openwb_pro", host="192.0.2.25")
    drv.http = FakeHttp({"/connect.php": {"plug_state": True, "charge_state": True, "power_all": 4140,
                                          "offered_current": 6, "currents": [6, 6, 6], "soc_value": 71}})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.soc == 71 and d.phases_active == 3
    await drv.set_phases(1)
    assert drv.http.calls[-1] == ("POST", "/connect.php", {"phasetarget": "1"})

    gen = make("http_wallbox", status_url="http://192.0.2.26/s", power_path="p", plugged_path="plug",
               current_url="http://192.0.2.26/c?a={current}", enable_url="http://192.0.2.26/on",
               disable_url="http://192.0.2.26/off")
    gen.http = FakeHttp({"http://192.0.2.26/s": {"p": 0, "plug": False}})
    assert (await gen.read_data()).state == WallboxState.IDLE
    await gen.set_current(9)
    assert gen.http.calls[-1][1] == "http://192.0.2.26/c?a=9"


# ---------------------------------------------------------------- MQTT

class FakeSub:
    def __init__(self, values):
        self.values = values
        self.published: list = []

    def get(self, topic, path, what):
        if topic not in self.values:
            raise ConnectionProblem(f"keine Daten auf {topic}")
        return float(self.values[topic])

    def get_optional(self, topic, path, what):
        return float(self.values[topic]) if topic and topic in self.values else None

    async def publish(self, topic, payload, *, retain=False):
        self.published.append((topic, payload, retain))


async def test_openwb_mqtt_isss():
    drv = make("openwb_mqtt", host="192.0.2.6", chargepoint="2")
    drv.sub = FakeSub({"openWB/lp/2/boolPlugStat": 1, "openWB/lp/2/boolChargeStat": 1, "openWB/lp/2/W": 4100,
                       "openWB/lp/2/APhase1": 6, "openWB/lp/2/APhase2": 6, "openWB/lp/2/APhase3": 6})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 4100
    await drv.set_current(10)
    await drv.heartbeat()
    await drv.stop_charging()
    assert drv.sub.published == [("openWB/set/isss/Lp2Current", "10", True),
                                 ("openWB/set/isss/heartbeat", "1", True),
                                 ("openWB/set/isss/Lp2Current", "0", True)]


async def test_mqtt_wallbox():
    drv = make("mqtt_wallbox", host="192.0.2.6", power_topic="wb/p", plugged_topic="wb/plug",
               current_topic="wb/set/i", enable_topic="wb/set/on")
    drv.sub = FakeSub({"wb/p": 0, "wb/plug": 1})
    assert (await drv.read_data()).state == WallboxState.CONNECTED
    await drv.set_current(8)
    await drv.start_charging()
    assert drv.sub.published == [("wb/set/i", "8", False), ("wb/set/on", "1", False)]


# ---------------------------------------------------------------- Wattpilot

def test_wattpilot_hash_and_auth_are_deterministic():
    hashed = hash_password("geheim", "12345678")
    assert len(hashed) == 32 and hashed == hash_password("geheim", "12345678")
    msg = auth_message(hashed, "t1", "t2", token3="t3")
    import hashlib
    hash1 = hashlib.sha256(("t1" + hashed).encode()).hexdigest()
    assert msg["hash"] == hashlib.sha256(("t3t2" + hash1).encode()).hexdigest()
    wrapped = secured(hashed, {"type": "setValue", "requestId": 4, "key": "amp", "value": 10})
    assert wrapped["type"] == "securedMsg" and wrapped["requestId"] == "4sm" and len(wrapped["hmac"]) == 64


async def test_wattpilot_status_and_set_value():
    drv = make("fronius_wattpilot", host="192.0.2.27", password="x")
    sent = []

    class Ws:
        async def send(self, raw):
            msg = json.loads(raw)
            sent.append(msg)
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(drv.handle({"type": "response", "requestId": msg["requestId"],
                                                          "success": True})))

    drv._ws = Ws()
    await drv.handle({"type": "hello", "serial": "123", "secured": False})
    await drv.handle({"type": "fullStatus", "status": {"car": 2, "amp": 12, "frc": 0, "wh": 3500,
                                                       "nrg": [230, 230, 230, 0, 12, 12, 12, 0, 0, 0, 0, 8280]}})
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 8280 and d.current_set == 12 and d.phases_active == 3
    await drv.set_current(10)
    assert sent[-1] == {"type": "setValue", "requestId": 1, "key": "amp", "value": 10}


async def test_wattpilot_without_data_is_offline():
    drv = make("fronius_wattpilot", host="192.0.2.27", password="x")
    with pytest.raises(ConnectionProblem):
        await drv.read_data()


async def test_wattpilot_unconfirmed_command():
    drv = make("fronius_wattpilot", host="192.0.2.27", password="x")

    class Ws:
        async def send(self, raw):
            pass

    drv._ws = Ws()
    import app.drivers.wattpilot as wp
    orig = wp.asyncio.wait_for

    async def fast(fut, timeout):
        return await orig(fut, timeout=0.05)

    wp.asyncio.wait_for = fast
    try:
        with pytest.raises(CommandNotApplied):
            await drv.stop_charging()
    finally:
        wp.asyncio.wait_for = orig


# ---------------------------------------------------------------- Cloud

async def test_easee_state_cache_and_commands():
    drv = make("easee", username="u", password="p", charger_id="EH000000")
    calls = []

    async def call(method, path, *, json=None, want="json"):
        calls.append((method, path, json))
        return {"chargerOpMode": 3, "totalPower": 7.2, "dynamicChargerCurrent": 10, "sessionEnergy": 4.0,
                "inCurrentT3": 10, "inCurrentT4": 10, "inCurrentT5": 10, "voltage": 231}

    drv.call = call
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == pytest.approx(7200) and d.current_set == 10
    await drv.read_data()
    assert len(calls) == 1  # zweites Lesen aus dem Zwischenspeicher
    await drv.set_current(12)
    await drv.stop_charging()
    assert calls[-2] == ("POST", "/chargers/EH000000/settings", {"dynamicChargerCurrent": 12.0})
    assert calls[-1][1] == "/chargers/EH000000/commands/pause_charging"
    await drv.read_data()
    assert len(calls) == 4  # nach Befehl neu gelesen


async def test_easee_offline_mode():
    drv = make("easee", username="u", password="p", charger_id="EH000000")

    async def call(method, path, *, json=None, want="json"):
        return {"chargerOpMode": 0}

    drv.call = call
    with pytest.raises(ConnectionProblem):
        await drv.read_data()


async def test_zaptec_observations_and_standalone():
    drv = make("zaptec", username="u", password="p", charger_id="abc")
    obs = [{"StateId": 710, "ValueAsString": "3"}, {"StateId": 513, "ValueAsString": "11000"},
           {"StateId": 708, "ValueAsString": "16"}, {"StateId": 553, "ValueAsString": "2.5"},
           {"StateId": 507, "ValueAsString": "16"}, {"StateId": 508, "ValueAsString": "16"},
           {"StateId": 509, "ValueAsString": "16"}]
    calls = []

    async def call(method, path, *, json=None, want="json"):
        calls.append((method, path, json))
        return obs

    drv.call = call
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 11000 and d.current_set == 16
    await drv.start_charging()
    assert calls[-1][1] == "/api/chargers/abc/sendCommand/507"
    obs.append({"StateId": 712, "ValueAsString": "true"})
    drv.invalidate()
    with pytest.raises(DriverError):
        await drv.read_data()
