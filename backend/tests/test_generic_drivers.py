"""Neue Treiber (3.0): HTTP/JSON, MQTT, Home Assistant, Tasmota, SG-Ready,
Victron GX, KEBA P30, Modbus-Registerprofil. Alle Gegenstellen sind Fakes."""
import json
import time

import pytest

from app.drivers import registry
from app.drivers.base import WallboxState
from app.drivers.config_validation import validate_config
from app.drivers.generic_common import extract, number, watts
from app.drivers.validation import CommandNotApplied, ConnectionProblem, DriverError

registry.discover()


def make(driver_id: str, **config):
    return registry.create_driver(driver_id, config)


def fake(obj, name, responses):
    """Async-Methode durch Antworten je Aufruf (dict: Schlüssel → Antwort) ersetzen."""
    calls = []

    async def method(path, *args, **kwargs):
        calls.append((path, args[0] if args else kwargs.get("params") or kwargs))
        value = responses(path, args[0] if args else None) if callable(responses) else responses
        return value

    setattr(obj, name, method)
    return calls


# ---------------------------------------------------------------- Hilfen

def test_extract_and_number():
    data = {"emeters": [{"power": "1.234,5"}], "on": "ON"}
    assert number(extract(data, "emeters.0.power")) == 1234.5
    assert number(extract(data, "on")) == 1.0
    assert watts("1,5", "kW") == 1500.0
    with pytest.raises(DriverError):
        extract(data, "emeters.3.power")
    with pytest.raises(DriverError):
        number("abc")


def test_every_driver_has_valid_defaults_and_placeholders():
    for meta in registry.all_metas():
        if meta.id.startswith("test_"):
            continue  # Testtreiber anderer Module
        cfg = {f.key: (f.default if f.default not in (None, "") else
                       (f.placeholder or ("1" if f.type.value == "number" else "x")))
               for f in meta.fields if f.required or f.default not in (None, "")}
        assert validate_config(meta, cfg) == [], meta.id
        assert meta.name and meta.description, meta.id
        assert "192.168." not in meta.model_dump_json(), meta.id


# ---------------------------------------------------------------- HTTP/JSON

async def test_http_meter_reads_path_and_inverts():
    drv = make("http_meter", url="http://192.0.2.10/status", power_path="data.p", scale=1, invert=True)
    fake(drv.http, "get_text", lambda p, a: json.dumps({"data": {"p": 812}}))
    assert (await drv.read_data()).grid_power == -812


async def test_http_meter_plain_number_and_auth_header():
    drv = make("http_meter", url="http://192.0.2.10/p", auth_header="Bearer abc")
    assert drv.http.headers == {"Authorization": "Bearer abc"}
    fake(drv.http, "get_text", lambda p, a: "  -1500 ")
    assert (await drv.read_data()).grid_power == -1500


async def test_http_inverter_optional_battery():
    drv = make("http_inverter", url="http://192.0.2.10/s", pv_path="pv", soc_path="bat.soc",
               battery_path="bat.p", battery_invert=True)
    fake(drv.http, "get_text", lambda p, a: json.dumps({"pv": 4200, "bat": {"soc": 55, "p": 900}}))
    d = await drv.read_data()
    assert (d.pv_power, d.battery_soc, d.battery_power, d.grid_power) == (4200, 55, -900, None)


async def test_http_relay_heater_switches_with_readback():
    drv = make("http_relay_heater", on_url="http://192.0.2.11/on", off_url="http://192.0.2.11/off",
               state_url="http://192.0.2.11/s", state_path="ison", rated_power=2000)
    state = {"ison": False}

    def respond(path, _):
        if path.endswith("/on"):
            state["ison"] = True
        elif path.endswith("/off"):
            state["ison"] = False
        return json.dumps(state)

    fake(drv.http, "get_text", respond)
    await drv.set_power(2000)
    d = await drv.read_data()
    assert d.is_on and d.power == 2000
    await drv.set_power(0)
    assert not (await drv.read_data()).is_on


async def test_http_relay_heater_detects_ignored_command():
    drv = make("http_relay_heater", on_url="http://192.0.2.11/on", off_url="http://192.0.2.11/off",
               state_url="http://192.0.2.11/s", state_path="ison")
    fake(drv.http, "get_text", lambda p, a: json.dumps({"ison": False}))
    with pytest.raises(CommandNotApplied):
        await drv.set_power(3000)


# ---------------------------------------------------------------- Home Assistant

def ha_states(states: dict):
    def respond(path, _=None):
        ent = path.rsplit("/", 1)[-1]
        state, unit = states[ent]
        return {"state": state, "attributes": {"unit_of_measurement": unit}}
    return respond


async def test_ha_meter_converts_kw_and_rejects_unavailable():
    drv = make("ha_meter", url="http://192.0.2.5:8123", token="t", grid_entity="sensor.netz")
    assert drv.ha.http.headers["Authorization"] == "Bearer t"
    fake(drv.ha.http, "get_json", ha_states({"sensor.netz": ("1.2", "kW")}))
    assert (await drv.read_data()).grid_power == 1200
    fake(drv.ha.http, "get_json", ha_states({"sensor.netz": ("unavailable", None)}))
    with pytest.raises(DriverError):
        await drv.read_data()


async def test_ha_wallbox_reads_and_commands():
    drv = make("ha_wallbox", url="http://192.0.2.5:8123", token="t", switch_entity="switch.laden",
               current_entity="number.strom", power_entity="sensor.leistung", plugged_entity="binary_sensor.auto",
               phases="3", max_current=16)
    fake(drv.ha.http, "get_json", ha_states({
        "switch.laden": ("on", None), "number.strom": ("10", "A"), "sensor.leistung": ("6900", "W"),
        "binary_sensor.auto": ("on", None)}))
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.current_set == 10 and d.phases_active == 3
    posts = fake(drv.ha.http, "post_json", [])
    await drv.set_current(32)
    await drv.stop_charging()
    assert posts[0] == ("/api/services/number/set_value", {"entity_id": "number.strom", "value": 16})
    assert posts[1] == ("/api/services/switch/turn_off", {"entity_id": "switch.laden"})


async def test_ha_wallbox_unplugged_is_idle():
    drv = make("ha_wallbox", url="http://192.0.2.5:8123", token="t", switch_entity="switch.laden",
               current_entity="number.strom", power_entity="sensor.leistung", plugged_entity="binary_sensor.auto")
    fake(drv.ha.http, "get_json", ha_states({
        "switch.laden": ("off", None), "number.strom": ("6", "A"), "sensor.leistung": ("0", "W"),
        "binary_sensor.auto": ("off", None)}))
    assert (await drv.read_data()).state == WallboxState.IDLE


# ---------------------------------------------------------------- Tasmota

async def test_tasmota_meter_and_relay():
    meter = make("tasmota_meter", host="192.0.2.12", power_path="SML.Power")
    fake(meter.http, "get_json", {"StatusSNS": {"SML": {"Power": -350}}})
    assert (await meter.read_data()).grid_power == -350

    relay = make("tasmota_relay_heater", host="192.0.2.12", relay=1, rated_power=2000)
    state = {"on": False}

    def respond(path, params):
        cmd = params["cmnd"]
        if cmd == "Power1 ON":
            state["on"] = True
        if cmd == "Power1 OFF":
            state["on"] = False
        if cmd == "Status 10":
            return {"StatusSNS": {"ENERGY": {"Power": 1980 if state["on"] else 0}}}
        return {"POWER1": "ON" if state["on"] else "OFF"}

    fake(relay.http, "get_json", respond)
    await relay.set_power(2000)
    d = await relay.read_data()
    assert d.is_on and d.power == 1980


# ---------------------------------------------------------------- SG-Ready

async def test_sgready_shelly_uses_recommendation_never_lock():
    drv = make("sgready_shelly", host="192.0.2.13", channel_1=0, channel_2=1, rated_power=2500)
    out = {0: False, 1: False}

    def respond(path, params):
        if path == "/rpc/Switch.Set":
            out[params["id"]] = params["on"]
            return {}
        return {"output": out[params["id"]]}

    fake(drv.http, "get_json", respond)
    await drv.set_power(2500)
    assert out == {0: False, 1: True}                 # Einschaltempfehlung, nie Sperre
    d = await drv.read_data()
    assert d.is_on and d.power == 2500 and d.extra["SG-Ready"] == "Einschaltempfehlung"
    await drv.set_power(0)
    assert out == {0: False, 1: False}


# ---------------------------------------------------------------- MQTT

async def test_mqtt_values_and_staleness():
    drv = make("mqtt_meter", host="192.0.2.6", grid_topic="haus/netz", grid_path="p", max_age_s=30, invert=False)
    drv.sub.values["haus/netz"] = (time.monotonic(), {"p": 420})
    assert (await drv.read_data()).grid_power == 420
    drv.sub.values["haus/netz"] = (time.monotonic() - 120, {"p": 420})
    with pytest.raises(ConnectionProblem):
        await drv.read_data()


async def test_mqtt_missing_topic_is_offline():
    drv = make("mqtt_inverter", host="192.0.2.6", pv_topic="pv/p")
    with pytest.raises(ConnectionProblem):
        await drv.read_data()


async def test_mqtt_relay_publishes_and_checks_state():
    drv = make("mqtt_relay_heater", host="192.0.2.6", command_topic="hs/set", state_topic="hs/state",
               payload_on="1", payload_off="0", rated_power=3000)
    sent = []

    async def publish(t, payload):
        sent.append((t, payload))
        drv.sub.values["hs/state"] = (time.monotonic(), payload)

    drv.sub.publish = publish
    drv.sub.values["hs/state"] = (time.monotonic(), "0")
    import asyncio as _a
    orig = _a.sleep

    async def no_sleep(_s):
        await orig(0)

    _a.sleep = no_sleep
    try:
        await drv.set_power(3000)
    finally:
        _a.sleep = orig
    assert sent == [("hs/set", "1")] and (await drv.read_data()).is_on


# ---------------------------------------------------------------- Modbus

class FakeConn:
    def __init__(self, regs: dict[int, int]):
        self.regs = regs

    async def read_holding(self, address, count=1):
        return [self.regs.get(address + i, 0) for i in range(count)]

    read_input = read_holding


async def test_victron_gx_sums_sources_and_signs():
    drv = make("victron_gx", host="192.0.2.14")
    regs = {808: 500, 809: 0xFFFF, 850: 3000, 820: 400, 821: (-100) & 0xFFFF, 822: 0,
            842: (-1200) & 0xFFFF, 843: 76}
    drv.conn = FakeConn(regs)
    d = await drv.read_data()
    assert d.pv_power == 3500 and d.grid_power == 300
    assert d.battery_power == -1200 and d.battery_soc == 76


async def test_generic_modbus_inverter_profile():
    drv = make("generic_modbus_inverter", host="192.0.2.31", pv_register=100, pv_type="u16", pv_scale=10,
               soc_register=200, soc_type="u16", battery_register=300, battery_type="s16", battery_invert=True)
    drv.conn = FakeConn({100: 420, 200: 64, 300: (-500) & 0xFFFF})
    d = await drv.read_data()
    assert d.pv_power == 4200 and d.battery_soc == 64 and d.battery_power == 500 and d.grid_power is None


async def test_generic_modbus_battery_implausible_value_is_reported():
    drv = make("generic_modbus_battery", host="192.0.2.31", soc_register=1, soc_type="u16",
               battery_register=2, battery_type="s16")
    drv.conn = FakeConn({1: 250, 2: 0})
    with pytest.raises(DriverError):
        await drv.read_data()


# ---------------------------------------------------------------- KEBA

async def test_keba_report_mapping_and_commands():
    drv = make("keba_p30", host="192.0.2.15", max_current=16, phases="3")
    sent = []

    async def send(command, *, expect_json):
        sent.append(command)
        if command == "report 2":
            return {"State": 3, "Plug": 7, "Curr user": 10000}
        if command == "report 3":
            return {"P": 6900000, "I1": 10000, "I2": 10000, "I3": 10000, "U1": 230, "E pres": 52000}
        return "TCH-OK :done"

    drv._send = send
    d = await drv.read_data()
    assert d.state == WallboxState.CHARGING and d.power == 6900 and d.current_set == 10
    assert d.phases_active == 3 and d.energy_session_kwh == pytest.approx(5.2)
    await drv.set_current(20)
    await drv.set_current(0)
    assert sent[-2:] == ["curr 16000", "ena 0"]
