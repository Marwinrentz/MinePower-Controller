"""Wärmepumpen und Klimaanlagen (3.4): Treiber Viessmann, Homey Pro, Daikin,
Verdichterschutz und Selbstregelung im Regler, Vorgaben aus dem Treiber."""
import json

import pytest

from app.core.loop import heat_defaults
from app.core.regulation import ChargeContext, WaterHeaterController, WaterHeaterSettings
from app.drivers import registry
from app.drivers.base import Maturity, WaterHeaterData
from app.drivers.daikin import parse
from app.drivers.validation import CommandNotApplied, DeviceRejected

registry.discover()


def make(driver_id: str, **config):
    return registry.create_driver(driver_id, config)


class FakeHttp:
    """Gegenstelle: `handler(method, path, params, body)` liefert die Antwort."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list = []

    async def get_json(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self.handler("GET", path, params, None)

    async def get_text(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self.handler("GET", path, params, None)

    async def request(self, method, path, *, params=None, json=None, data=None, headers=None, want="json"):
        self.calls.append((method, path, json if json is not None else data))
        return self.handler(method, path, params, json if json is not None else data)


# ---------------------------------------------------------------- Registry

def test_new_drivers_registered_experimental_and_relay():
    for driver_id in ("viessmann_vicare", "homey_pro", "daikin_local"):
        cls = registry.get_driver_class(driver_id)
        assert cls.meta.category.value == "water_heater"
        assert cls.meta.maturity == Maturity.EXPERIMENTAL
        assert cls.modulating is False


def test_secrets_are_password_fields():
    from app.core.secrets import device_secret_keys
    assert device_secret_keys("viessmann_vicare") == {"refresh_token"}
    assert device_secret_keys("homey_pro") == {"api_key"}


# ---------------------------------------------------------------- Daikin

class FakeDaikin:
    def __init__(self, **ctl):
        self.ctl = {"pow": "0", "mode": "3", "stemp": "24.0", "shum": "0", "f_rate": "A", "f_dir": "0", **ctl}
        self.sets: list[dict] = []

    def __call__(self, method, path, params, body):
        if path.endswith("get_control_info"):
            return "ret=OK," + ",".join(f"{k}={v}" for k, v in self.ctl.items())
        if path.endswith("get_sensor_info"):
            return "ret=OK,htemp=21.5,hhum=-,otemp=4.0,err=0,cmpfreq=" + ("32" if self.ctl["pow"] == "1" else "0")
        if path.endswith("set_control_info"):
            self.sets.append(dict(params))
            self.ctl.update({k: str(v) for k, v in params.items()})
            return "ret=OK"
        raise AssertionError(path)


def test_daikin_parse():
    assert parse("ret=OK,pow=1,mode=4,stemp=21.0") == {"ret": "OK", "pow": "1", "mode": "4", "stemp": "21.0"}


async def test_daikin_switches_on_and_off_only_what_it_switched_on():
    fake = FakeDaikin()
    drv = make("daikin_local", host="192.0.2.40", action="power", surplus_mode="heat", surplus_temp=23,
               rated_power=900)
    drv.http = FakeHttp(fake)
    d = await drv.read_data()
    assert d.power == 0 and d.is_on is False and d.temperature_c == 21.5
    await drv.set_power(900)
    assert fake.sets[-1]["pow"] == "1" and fake.sets[-1]["mode"] == "4" and fake.sets[-1]["stemp"] == "23.0"
    # Alle Grundwerte mitgeschickt
    assert {"shum", "f_rate", "f_dir"} <= set(fake.sets[-1])
    d = await drv.read_data()
    assert d.power == 900 and d.is_on
    await drv.set_power(0)
    assert fake.sets[-1]["pow"] == "0"


async def test_daikin_never_switches_off_a_unit_the_user_turned_on():
    fake = FakeDaikin(pow="1", mode="3", stemp="24.0")
    drv = make("daikin_local", host="192.0.2.40", action="power", surplus_mode="cool", surplus_temp=24)
    drv.http = FakeHttp(fake)
    await drv.set_power(0)
    assert fake.sets == []
    await drv.set_power(5000)  # läuft schon im gewünschten Zustand: nichts senden
    assert fake.sets == []
    await drv.set_power(0)
    assert fake.sets == [] and fake.ctl["pow"] == "1"


async def test_daikin_setpoint_mode_restores_previous_temperature():
    fake = FakeDaikin(pow="1", mode="4", stemp="21.0")
    drv = make("daikin_local", host="192.0.2.40", action="setpoint", surplus_temp=23)
    drv.http = FakeHttp(fake)
    await drv.set_power(2000)
    assert fake.ctl["stemp"] == "23.0" and fake.ctl["pow"] == "1"
    await drv.set_power(0)
    assert fake.ctl["stemp"] == "21.0"


async def test_daikin_airbase_prefix_and_rejection():
    def handler(method, path, params, body):
        assert path.startswith("/skyfi/")
        return "ret=PARAM NG"
    drv = make("daikin_local", host="192.0.2.41", variant="airbase")
    drv.http = FakeHttp(handler)
    with pytest.raises(DeviceRejected):
        await drv.read_data()


# ---------------------------------------------------------------- Homey

class FakeHomey:
    def __init__(self, caps: dict, power_dev: dict | None = None):
        self.caps = {k: {"value": v} for k, v in caps.items()}
        self.power_dev = power_dev
        self.puts: list = []

    def __call__(self, method, path, params, body):
        if method == "GET" and path.endswith("/power-1"):
            return {"name": "Zwischenstecker", "capabilitiesObj": {"measure_power": {"value": self.power_dev}}}
        if method == "GET":
            return {"name": "Wärmepumpe", "available": True, "capabilitiesObj": self.caps}
        if method == "PUT":
            cap = path.rsplit("/", 1)[1]
            self.puts.append((cap, body["value"]))
            self.caps[cap] = {"value": body["value"]}
            return None
        raise AssertionError((method, path))


async def test_homey_onoff_and_measured_power():
    fake = FakeHomey({"onoff": False, "measure_power": 3.0, "measure_temperature": 47.5})
    drv = make("homey_pro", host="192.0.2.50", api_key="k", device_id="hp-1", rated_power=1500)
    drv.http = FakeHttp(fake)
    d = await drv.read_data()
    assert d.power == 3.0 and d.is_on is False and d.temperature_c == 47.5
    await drv.set_power(1500)
    assert fake.puts == [("onoff", True)]
    await drv.set_power(0)
    assert fake.puts[-1] == ("onoff", False)


async def test_homey_leaves_user_switched_device_on():
    fake = FakeHomey({"onoff": True})
    drv = make("homey_pro", host="192.0.2.50", api_key="k", device_id="hp-1")
    drv.http = FakeHttp(fake)
    await drv.set_power(0)
    await drv.set_power(1500)
    await drv.set_power(0)
    assert fake.puts == []
    # Ohne Messung: Nennleistung, solange an
    assert (await drv.read_data()).power == 1500


async def test_homey_separate_power_meter_and_target_mode():
    fake = FakeHomey({"target_temperature": 20.0, "measure_temperature": 20.4}, power_dev=820.0)
    drv = make("homey_pro", host="192.0.2.50", api_key="k", device_id="hp-1", power_device_id="power-1",
               action="target", surplus_temp=22.5)
    drv.http = FakeHttp(fake)
    assert (await drv.read_data()).power == 820.0
    await drv.set_power(5000)
    assert fake.puts == [("target_temperature", 22.5)]
    await drv.set_power(0)
    assert fake.puts[-1] == ("target_temperature", 20.0)


async def test_homey_onoff_without_capability_is_a_clear_error():
    drv = make("homey_pro", host="192.0.2.50", api_key="k", device_id="hp-1")
    drv.http = FakeHttp(FakeHomey({"target_temperature": 20.0}))
    with pytest.raises(DeviceRejected, match="onoff"):
        await drv.set_power(1500)


async def test_homey_onoff_not_applied():
    def handler(method, path, params, body):
        return {"name": "x", "capabilitiesObj": {"onoff": {"value": False}}}
    drv = make("homey_pro", host="192.0.2.50", api_key="k", device_id="hp-1")
    drv.http = FakeHttp(handler)
    with pytest.raises(CommandNotApplied):
        await drv.set_power(1500)


# ---------------------------------------------------------------- Viessmann

class FakeViessmann:
    def __init__(self, once=False, compressor=False, target=50):
        self.once = once
        self.compressor = compressor
        self.target = target
        self.commands: list = []
        self.logins = 0

    def __call__(self, method, path, params, body):
        if path.endswith("/idp/v3/token"):
            self.logins += 1
            assert body["grant_type"] == "refresh_token" and body["refresh_token"] == "rt"
            return {"access_token": "at", "expires_in": 3600}
        if "equipment/installations" in path:
            assert "/iot/v2/equipment/installations" in path  # v1 seit 2025 abgeschaltet (HTTP 410)
            return {"data": [{"id": 4711, "gateways": [{"serial": "7637415000000000"}]}]}
        if method == "GET" and path.endswith("/features"):
            assert "/iot/v2/features/installations/4711/gateways/7637415000000000/devices/0/features" in path
            return {"data": [
                {"feature": "heating.dhw.sensors.temperature.dhwCylinder", "properties": {"value": {"value": 48.2}}},
                {"feature": "heating.compressors.0", "properties": {"active": {"value": self.compressor}}},
                {"feature": "heating.dhw.oneTimeCharge", "properties": {"active": {"value": self.once}}},
                {"feature": "heating.dhw.temperature.main", "properties": {"value": {"value": self.target}}},
            ]}
        if method == "POST" and "/commands/" in path:
            feature, command = path.rsplit("/features/", 1)[1].split("/commands/")
            self.commands.append((feature, command, body))
            if command == "activate":
                self.once = True
            if command == "deactivate":
                self.once = False
            if command == "setTargetTemperature":
                self.target = body["temperature"]
            return {"data": {"success": True}}
        raise AssertionError((method, path))


async def test_viessmann_discovers_reads_and_caches():
    fake = FakeViessmann(compressor=True)
    drv = make("viessmann_vicare", client_id="c", refresh_token="rt", rated_power=2100, poll_s=120)
    drv.http = FakeHttp(fake)
    d = await drv.read_data()
    assert d.temperature_c == 48.2 and d.power == 2100 and d.extra["Verdichter"] == "läuft"
    n = len(drv.http.calls)
    await drv.read_data()
    assert len(drv.http.calls) == n  # Kontingent: innerhalb poll_s keine neue Abfrage
    assert fake.logins == 1


async def test_viessmann_one_time_charge_on_off():
    fake = FakeViessmann()
    drv = make("viessmann_vicare", client_id="c", refresh_token="rt", rated_power=2000)
    drv.http = FakeHttp(fake)
    await drv.read_data()
    await drv.set_power(0)  # nichts freigegeben: kein Befehl
    assert fake.commands == []
    await drv.set_power(2000)
    assert fake.commands[-1][:2] == ("heating.dhw.oneTimeCharge", "activate")
    await drv.read_data()
    await drv.set_power(0)
    assert fake.commands[-1][:2] == ("heating.dhw.oneTimeCharge", "deactivate")


async def test_viessmann_dhw_temp_raises_and_restores():
    fake = FakeViessmann(target=48)
    drv = make("viessmann_vicare", client_id="c", refresh_token="rt", action="dhw_temp", surplus_temp=58,
               installation_id="4711", gateway_serial="7637415000000000")
    drv.http = FakeHttp(fake)
    await drv.set_power(3000)
    assert fake.commands[-1] == ("heating.dhw.temperature.main", "setTargetTemperature", {"temperature": 58})
    await drv.read_data()
    await drv.set_power(0)
    assert fake.commands[-1] == ("heating.dhw.temperature.main", "setTargetTemperature", {"temperature": 48})


async def test_viessmann_needs_credentials():
    drv = make("viessmann_vicare", client_id="", refresh_token="")
    drv.http = FakeHttp(FakeViessmann())
    with pytest.raises(DeviceRejected, match="Client-ID"):
        await drv.read_data()


def test_vicare_token_pkce():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "vicare_token", pathlib.Path(__file__).parents[1] / "tools" / "vicare_token.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    verifier, challenge = mod.pkce()
    assert 43 <= len(verifier) <= 128 and "=" not in challenge


# ---------------------------------------------------------------- Vorgaben

def test_heat_defaults_for_heat_pump_and_relay():
    hp = make("homey_pro", host="192.0.2.50", api_key="k", device_id="x", rated_power=1500)
    out = heat_defaults(hp, {"mode": "pv_only", "max_power_w": 3000, "modulating": True})
    assert out["kind"] == "heat_pump" and out["self_regulating"] is True
    assert out["min_on_s"] == 600 and out["min_off_s"] == 300
    assert out["modulating"] is False and out["max_power_w"] == 1500
    # Gespeicherte Werte des Nutzers gelten
    out = heat_defaults(hp, {"self_regulating": False, "min_on_s": 0})
    assert out["self_regulating"] is False and out["min_on_s"] == 0
    ac = make("daikin_local", host="192.0.2.40")
    assert heat_defaults(ac, {})["kind"] == "climate"
    sg = make("sgready_shelly", host="192.0.2.13")
    assert heat_defaults(sg, {})["kind"] == "heat_pump"
    # Heizstab (modulierend) bleibt unverändert
    rod = make("sim_water_heater")
    assert heat_defaults(rod, {"modulating": True}) == {"modulating": True}


# ---------------------------------------------------------------- Regler

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def ctx():
    from datetime import datetime
    return ChargeContext(now=datetime(2026, 6, 1, 12, 0))


def hp_controller(clock, **kw):
    s = WaterHeaterSettings.from_dict({"mode": "pv_only", "max_power_w": 1500, "modulating": False,
                                       "start_delay_s": 0, "stop_delay_s": 0, "self_regulating": True,
                                       "min_on_s": 600, "min_off_s": 300, "kind": "heat_pump", **kw})
    return WaterHeaterController(s, clock)


def test_min_run_time_holds_through_cloud():
    clock = Clock()
    c = hp_controller(clock)
    data = WaterHeaterData(power=0, temperature_c=21.0)
    assert c.decide(2000, data, ctx()).power_w == 1500
    clock.t += 60
    d = c.decide(0, data, ctx())
    assert d.power_w == 1500 and "Mindestlaufzeit" in d.reason
    clock.t += 600
    assert c.decide(0, data, ctx()).power_w == 0


def test_min_pause_blocks_restart():
    clock = Clock()
    c = hp_controller(clock, min_on_s=0)
    data = WaterHeaterData(power=0)
    assert c.decide(2000, data, ctx()).power_w == 1500
    clock.t += 10
    assert c.decide(0, data, ctx()).power_w == 0
    clock.t += 10
    d = c.decide(3000, data, ctx())
    assert d.power_w == 0 and "Mindestpause" in d.reason
    clock.t += 300
    assert c.decide(3000, data, ctx()).power_w == 1500


def test_off_beats_min_run_time():
    clock = Clock()
    c = hp_controller(clock)
    assert c.decide(2000, WaterHeaterData(), ctx()).power_w == 1500
    c.s.mode = "off"
    assert c.decide(2000, WaterHeaterData(), ctx()).power_w == 0


def test_self_regulating_ignores_room_temperature():
    clock = Clock()
    # Raumtemperatur 22 °C, Zieltemperatur (für Wasser) 20 °C: ein Heizstab
    # würde stoppen, die Klimaanlage regelt selbst und bekommt Freigabe.
    c = hp_controller(clock, target_temp_c=20)
    assert c.decide(2000, WaterHeaterData(temperature_c=22.0), ctx()).power_w == 1500
    rod = WaterHeaterController(WaterHeaterSettings.from_dict(
        {"mode": "pv_only", "target_temp_c": 20, "start_delay_s": 0}), clock)
    assert rod.decide(2000, WaterHeaterData(temperature_c=22.0), ctx()).power_w == 0


def test_settings_bounds():
    s = WaterHeaterSettings.from_dict({"min_on_s": 99999, "min_off_s": -5, "kind": "rakete"})
    assert s.min_on_s == 0 or s.min_on_s <= 7200
    assert s.min_off_s == 0 and s.kind == "water"


def test_simulated_heat_pump_draws_power_only_when_allowed_or_cold():
    from app.drivers.simulation import SimulationWorld
    w = SimulationWorld()
    w.hp_temp = 50.0
    w._last_tick -= 10
    w.tick()
    assert w.hp_power == 0
    w.hp_allowed = True
    w._last_tick -= 10
    w.tick()
    assert w.hp_power == w.hp_rated


def test_validate_heat_settings():
    from fastapi import HTTPException

    from app.api.devices import validate_device_settings
    validate_device_settings("water_heater", {"kind": "climate", "min_on_s": 600, "self_regulating": True})
    for bad in ({"kind": "x"}, {"min_on_s": 99999}, {"self_regulating": "ja"}):
        with pytest.raises(HTTPException):
            validate_device_settings("water_heater", dict(bad))


def test_json_roundtrip_of_payload_fields():
    s = WaterHeaterSettings.from_dict({"kind": "climate", "self_regulating": True, "min_on_s": 300})
    assert json.loads(json.dumps({"kind": s.kind, "min_on_s": s.min_on_s}))["kind"] == "climate"
