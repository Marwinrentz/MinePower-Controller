"""Daikin Onecta (Cloud), rotierende Refresh-Tokens, 'Verbinden'-Knopf (OAuth)
und Einrichtungsanleitungen (3.5)."""
import urllib.parse

import pytest

from app.api import oauth as oauth_api
from app.db import async_session
from app.drivers import daikin_onecta as onecta
from app.drivers import oauth_store, registry
from app.drivers.validation import ConnectionProblem, DeviceRejected
from app.models import Device, User
from app.security import create_token, hash_password

from .asgi import request

registry.discover()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth_store, "_dir", lambda: tmp_path / "oauth")
    onecta._accounts.clear()
    oauth_api._pending.clear()
    yield
    onecta._accounts.clear()


def mp_climate(on="off", mode="heating", room=21.0, target=21.0, powerful="off"):
    return {
        "embeddedId": "climateControl", "managementPointType": "climateControl",
        "onOffMode": {"settable": True, "value": on, "values": ["on", "off"]},
        "operationMode": {"settable": True, "value": mode, "values": ["heating", "cooling", "auto"]},
        "powerfulMode": {"settable": True, "value": powerful, "values": ["on", "off"]},
        "temperatureControl": {"settable": True, "value": {"operationModes": {
            "heating": {"setpoints": {"roomTemperature": {"settable": True, "value": target, "minValue": 10,
                                                          "maxValue": 30, "stepValue": 0.5}}},
            "cooling": {"setpoints": {"roomTemperature": {"settable": True, "value": 25, "minValue": 18,
                                                          "maxValue": 32, "stepValue": 0.5}}}}}},
        "sensoryData": {"settable": False, "value": {"roomTemperature": {"value": room},
                                                      "outdoorTemperature": {"value": 4.5}}},
    }


def mp_dhw(tank=46.0, target=48, powerful=False):
    return {
        "embeddedId": "domesticHotWaterTank", "managementPointType": "domesticHotWaterTank",
        "onOffMode": {"settable": True, "value": "on"},
        "powerfulMode": {"settable": True, "value": "on" if powerful else "off"},
        "isPowerfulModeActive": {"settable": False, "value": powerful},
        "temperatureControl": {"settable": True, "value": {"operationModes": {"heating": {"setpoints": {
            "domesticHotWaterTemperature": {"settable": True, "value": target, "minValue": 30, "maxValue": 60,
                                            "stepValue": 1}}}}}},
        "sensoryData": {"settable": False, "value": {"tankTemperature": {"value": tank}}},
    }


class FakeOnecta:
    """Gegenstelle für Token-Endpunkt und /v1/gateway-devices."""

    def __init__(self, devices, rotate=True):
        self.devices = devices
        self.rotate = rotate
        self.logins: list[str] = []
        self.gets = 0
        self.patches: list[tuple[str, dict]] = []
        self.calls: list = []
        self.last_headers = {"X-RateLimit-Remaining-Day": "150"}
        self.status_429 = False
        self._n = 0

    async def connect(self):
        pass

    async def close(self):
        pass

    async def request(self, method, path, *, params=None, json=None, data=None, headers=None, want="json"):
        self.calls.append((method, path))
        if path == onecta.TOKEN_URL:
            self.logins.append(data["refresh_token"])
            self._n += 1
            res = {"access_token": f"at{self._n}", "expires_in": 3600}
            if self.rotate:
                res["refresh_token"] = f"rt{self._n}"
            return res
        if self.status_429:
            self.last_headers = {"Retry-After": "600"}
            raise DeviceRejected("Daikin Onecta: HTTP 429 von x")
        if method == "GET" and path == "/v1/gateway-devices":
            self.gets += 1
            return self.devices
        if method == "PATCH":
            self.patches.append((path, json))
            dev_id, rest = path.split("/v1/gateway-devices/")[1].split("/management-points/")
            mp_id, name = rest.split("/characteristics/")
            for d in self.devices:
                if d["id"] != dev_id:
                    continue
                for mp in d["managementPoints"]:
                    if mp["embeddedId"] == mp_id:
                        if "path" in json:
                            node = mp[name]["value"]
                            for key in json["path"].strip("/").split("/"):
                                node = node[key]
                            node["value"] = json["value"]
                        else:
                            mp[name]["value"] = json["value"]
            return None
        raise AssertionError((method, path))


def make(**config):
    base = {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt0", "rated_power": 900}
    base.update(config)
    return registry.create_driver("daikin_onecta", base)


def device(dev_id="d-1", *mps, model="dx4"):
    return {"id": dev_id, "deviceModel": model, "isCloudConnectionUp": True,
            "managementPoints": [{"embeddedId": "gateway", "managementPointType": "gateway"}, *mps]}


# ---------------------------------------------------------------- Lesen

async def test_reads_climate_and_caches_within_poll():
    fake = FakeOnecta([device("d-1", mp_climate(on="on", room=21.5))])
    drv = make()
    drv.http = fake
    d = await drv.read_data()
    assert d.power == 900 and d.temperature_c == 21.5 and d.is_on
    assert d.extra["Außen"] == "4.5 °C" and d.extra["Anfragen heute übrig"] == 150
    await drv.read_data()
    assert fake.gets == 1  # 200 Anfragen am Tag: innerhalb poll_s nicht neu lesen


async def test_two_devices_on_one_account_share_login_and_poll():
    fake = FakeOnecta([device("ac", mp_climate()), device("alt", mp_climate(), mp_dhw(), model="Altherma")])
    ac = make(device_id="ac")
    tank = make(device_id="alt", target="dhw", action="powerful")
    assert ac.account is tank.account
    ac.http = fake
    await ac.read_data()
    t = await tank.read_data()
    assert fake.gets == 1 and len(fake.logins) == 1
    assert t.temperature_c == 46.0 and tank.heat_kind == "heat_pump"


async def test_rotating_refresh_token_is_persisted_and_used_after_restart():
    fake = FakeOnecta([device("d-1", mp_climate())])
    drv = make()
    drv.http = fake
    await drv.read_data()
    assert fake.logins == ["rt0"]
    # Neustart: neuer Prozess, gleiche Konfiguration (dort steht weiter rt0)
    onecta._accounts.clear()
    drv2 = make()
    drv2.http = fake
    drv2.account.poll_s = 0
    await drv2.read_data()
    assert fake.logins == ["rt0", "rt1"]  # das gespeicherte, nicht das verbrauchte
    stored = (oauth_store._dir() / f"{drv2.account.key}.json").read_text()
    assert "rt2" not in stored and "enc:v1:" in stored  # verschlüsselt


async def test_rate_limit_blocks_further_requests():
    fake = FakeOnecta([device("d-1", mp_climate())])
    drv = make()
    drv.http = fake
    await drv.read_data()
    fake.status_429 = True
    drv.account._fetched_at = 0.0  # fällig
    d = await drv.read_data()  # in der Sperre: letzter Stand statt Fehler
    assert d is not None and drv.account.blocked() > 500
    n = len(fake.calls)
    with pytest.raises(ConnectionProblem, match="kontingent"):
        await drv.account.call("GET", "/v1/gateway-devices")
    assert len(fake.calls) == n  # gesperrt: keine Anfrage


# ---------------------------------------------------------------- Steuern

async def test_power_switches_on_with_mode_and_setpoint_and_off_again():
    fake = FakeOnecta([device("d-1", mp_climate(on="off", mode="cooling"))])
    drv = make(action="power", surplus_mode="heating", surplus_temp=23.3)
    drv.http = fake
    await drv.read_data()
    await drv.set_power(900)
    names = [(p.rsplit("/", 1)[1], b) for p, b in fake.patches]
    assert names == [("operationMode", {"value": "heating"}),
                     ("temperatureControl", {"value": 23.5, "path": "/operationModes/heating/setpoints/roomTemperature"}),
                     ("onOffMode", {"value": "on"})]
    d = await drv.read_data()  # ohne neue Abfrage: Annahme bis zum nächsten Lesen
    assert d.is_on and d.power == 900 and fake.gets == 1
    await drv.set_power(900)  # keine Änderung: kein Befehl (Kontingent)
    assert len(fake.patches) == 3
    await drv.set_power(0)
    assert fake.patches[-1][1] == {"value": "off"}


async def test_never_switches_off_a_unit_the_user_turned_on():
    fake = FakeOnecta([device("d-1", mp_climate(on="on"))])
    drv = make(action="power")
    drv.http = fake
    await drv.read_data()
    await drv.set_power(900)
    await drv.set_power(0)
    assert fake.patches == []


async def test_dhw_powerful_boost_on_off():
    fake = FakeOnecta([device("alt", mp_climate(), mp_dhw(), model="Altherma")])
    drv = make(target="dhw", action="power")  # 'Einschalten' beim Speicher = Boost
    drv.http = fake
    assert drv.action == "powerful"
    await drv.read_data()
    await drv.set_power(900)
    path = "/v1/gateway-devices/alt/management-points/domesticHotWaterTank/characteristics/powerfulMode"
    assert fake.patches[-1] == (path, {"value": "on"})
    d = await drv.read_data()
    assert d.power == 900 and d.extra["Powerful"] == "aktiv"
    await drv.set_power(0)
    assert fake.patches[-1][1] == {"value": "off"}


async def test_dhw_setpoint_raises_clamped_and_restores():
    fake = FakeOnecta([device("alt", mp_dhw(target=48), model="Altherma")])
    drv = make(target="dhw", action="setpoint", surplus_temp=65)
    drv.http = fake
    await drv.read_data()
    await drv.set_power(900)
    assert fake.patches[-1][1] == {"value": 60, "path": "/operationModes/heating/setpoints/domesticHotWaterTemperature"}
    await drv.set_power(0)
    assert fake.patches[-1][1]["value"] == 48


async def test_missing_management_point_and_credentials():
    fake = FakeOnecta([device("d-1", mp_climate())])
    drv = make(target="dhw", action="powerful")
    drv.http = fake
    with pytest.raises(DeviceRejected, match="Warmwasserspeicher"):
        await drv.read_data()
    onecta._accounts.clear()
    drv = make(client_secret="")
    drv.http = fake
    with pytest.raises(DeviceRejected, match="Client-Secret"):
        await drv.read_data()


async def test_test_command_lists_devices():
    fake = FakeOnecta([device("d-1", mp_climate()), device("d-2", mp_climate(), model="Perfera")])
    drv = make()
    drv.http = fake
    res = await drv.test_command()
    assert res["ok"] and "Perfera = d-2" in res["message"] and res["message"].startswith("Anmeldung")
    assert "2 –" in res["message"]


async def test_room_names_from_app_are_shown():
    a, b = mp_climate(), mp_climate()
    a["name"] = {"settable": True, "value": "Daikin Schlafzimmer"}
    b["name"] = {"settable": True, "value": "Daikin Wohnzimmer"}
    fake = FakeOnecta([device("s", a), device("w", b)])
    drv = make(device_id="w")
    drv.http = fake
    data = await drv.read_data()
    assert data.extra["Gerät"] == "Daikin Wohnzimmer (dx4)"
    assert data.extra["Geräte im Konto"].startswith("2: Daikin Schlafzimmer (dx4)")
    res = await drv.test_command()
    assert "Daikin Schlafzimmer (dx4) = s" in res["message"] and "Daikin Wohnzimmer (dx4) = w" in res["message"]


async def test_connection_test_offers_all_matching_devices_as_choices():
    a, b = mp_climate(), mp_climate()
    a["name"] = {"settable": True, "value": "Daikin Schlafzimmer"}
    b["name"] = {"settable": True, "value": "Daikin Wohnzimmer"}
    fake = FakeOnecta([device("s", a), device("w", b), device("t", mp_dhw(), model="Altherma")])
    drv = make()
    drv.http = fake
    res = await drv.test_connection()
    assert res.ok
    assert res.choices == {"device_id": [
        {"value": "s", "label": "Daikin Schlafzimmer (dx4)"},
        {"value": "w", "label": "Daikin Wohnzimmer (dx4)"},
    ]}


# ---------------------------------------------------------------- Anleitungen

def test_every_driver_with_secret_has_setup_steps():
    missing = [m.id for m in registry.all_metas()
               if any(f.type.value == "password" for f in m.fields) and not m.setup]
    assert missing == []


def test_oauth_drivers_have_button():
    assert registry.get_driver_class("viessmann_vicare").meta.oauth == "viessmann"
    assert registry.get_driver_class("daikin_onecta").meta.oauth == "daikin_onecta"
    assert registry.get_driver_class("daikin_onecta").meta.maturity.value == "experimental"


async def test_homey_without_device_id_lists_devices():
    from .test_heat_pumps import FakeHttp
    drv = registry.create_driver("homey_pro", {"host": "192.0.2.50", "api_key": "k"})
    drv.http = FakeHttp(lambda m, p, q, b: {"a1": {"id": "a1", "name": "Wärmepumpe"},
                                             "b2": {"id": "b2", "name": "Klima"}})
    with pytest.raises(DeviceRejected, match="Klima = b2; Wärmepumpe = a1"):
        await drv.read_data()


# ---------------------------------------------------------------- OAuth-API

@pytest.fixture
async def admin_token():
    from app.db import Base, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        user = User(email="admin@example.com", name="A", password_hash=hash_password("geheim-1234"), role="admin")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        viewer = User(email="ro@example.com", name="R", password_hash=hash_password("geheim-1234"), role="readonly")
        db.add(viewer)
        await db.commit()
        await db.refresh(viewer)
    return create_token(user), create_token(viewer)


async def test_oauth_start_builds_url_and_finish_exchanges_code(admin_token, monkeypatch):
    token, _ = admin_token
    status, _, res = await request("POST", "/api/oauth/start", token=token, body={
        "provider": "viessmann", "client_id": "cid", "redirect_uri": "http://192.168.1.5:8000/oauth/callback"})
    assert status == 200, res
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(res["auth_url"]).query)
    assert res["auth_url"].startswith("https://iam.viessmann-climatesolutions.com/idp/v3/authorize?")
    assert q["state"] == [res["state"]] and q["code_challenge_method"] == ["S256"]

    seen = {}

    async def fake_exchange(provider, pending, code):
        seen.update(code=code, verifier=pending.verifier, redirect=pending.redirect_uri)
        return {"refresh_token": "neu-rt", "access_token": "x"}

    monkeypatch.setattr(oauth_api, "_exchange", fake_exchange)
    back = f"http://192.168.1.5:8000/oauth/callback?code=abc&state={res['state']}"
    status, _, out = await request("POST", "/api/oauth/finish", token=token, body={"code_or_url": back})
    assert status == 200 and out["refresh_token"] == "neu-rt" and out["field"] == "refresh_token"
    assert seen["code"] == "abc" and seen["verifier"] and seen["redirect"].endswith("/oauth/callback")
    # Einmalig: derselbe Vorgang geht kein zweites Mal
    status, _, _ = await request("POST", "/api/oauth/finish", token=token, body={"code_or_url": back})
    assert status == 422


async def test_oauth_daikin_rules_and_stored_secret(admin_token):
    token, readonly = admin_token
    body = {"provider": "daikin_onecta", "client_id": "cid", "client_secret": "s",
            "redirect_uri": "http://192.168.1.5/oauth/callback"}
    status, _, res = await request("POST", "/api/oauth/start", token=token, body=body)
    assert status == 422 and "https" in res["detail"]
    body["redirect_uri"] = "https://localhost/oauth/callback"
    status, _, res = await request("POST", "/api/oauth/start", token=token, body=body)
    assert status == 422 and "localhost" in res["detail"]
    body.update(redirect_uri="https://192.168.1.5:8000/oauth/callback", client_secret="")
    status, _, res = await request("POST", "/api/oauth/start", token=token, body=body)
    assert status == 422 and "Client-Secret" in res["detail"]
    # Gespeichertes Gerät: '•••' heißt gespeichertes Secret
    from app.core.secrets import protect_config
    async with async_session() as db:
        dev = Device(name="Klima", category="water_heater", driver_id="daikin_onecta",
                     config=protect_config("daikin_onecta", {"client_id": "cid", "client_secret": "geheim",
                                                             "refresh_token": "rt"}), settings={})
        db.add(dev)
        await db.commit()
        await db.refresh(dev)
    body.update(client_secret="•••", device_id=dev.id)
    status, _, res = await request("POST", "/api/oauth/start", token=token, body=body)
    assert status == 200 and "client_secret" not in res["auth_url"]
    assert oauth_api._pending[res["state"]].client_secret == "geheim"
    status, _, _ = await request("POST", "/api/oauth/start", token=readonly, body=body)
    assert status == 403


async def test_oauth_finish_reports_provider_error(admin_token):
    token, _ = admin_token
    status, _, res = await request("POST", "/api/oauth/finish", token=token, body={
        "code_or_url": "https://x/oauth/callback?error=access_denied&state=abc"})
    assert status == 422 and "access_denied" in res["detail"]
