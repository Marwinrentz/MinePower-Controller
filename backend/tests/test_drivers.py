"""Unit-Tests für Treiber-Hilfslogik (ohne Hardware)."""
from app.drivers.base import WallboxState
from app.drivers.tesla.clients import _apply_proxy_port, unwrap_vehicle_data
from app.drivers.tesla.vehicle import TeslaVehicle


def test_unwrap_ble_proxy_double_nested():
    # TeslaBleHttpProxy verschachtelt response.response.charge_state
    raw = {"response": {"result": True, "vin": "X", "command": "vehicle_data",
                        "response": {"charge_state": {"battery_level": 37, "charger_power": 11}}}}
    out = unwrap_vehicle_data(raw)
    assert out["charge_state"]["battery_level"] == 37


def test_unwrap_fleet_single_nested():
    # Fleet-API direkt: nur eine Ebene
    raw = {"response": {"charge_state": {"battery_level": 55}}}
    out = unwrap_vehicle_data(raw)
    assert out["charge_state"]["battery_level"] == 55


def test_unwrap_empty():
    assert unwrap_vehicle_data({}) == {}
    assert unwrap_vehicle_data({"response": None}) == {}


def test_proxy_port_appended_when_missing():
    assert _apply_proxy_port("http://192.168.1.99", 8080) == "http://192.168.1.99:8080"


def test_proxy_port_added_scheme_when_missing():
    assert _apply_proxy_port("192.168.1.99", 8080) == "http://192.168.1.99:8080"


def test_explicit_port_in_url_wins():
    assert _apply_proxy_port("http://192.168.1.99:9000", 8080) == "http://192.168.1.99:9000"


def test_no_port_config_keeps_url():
    # ohne proxy_port bleibt die URL unverändert (Port 80 implizit)
    assert _apply_proxy_port("http://192.168.1.99", None) == "http://192.168.1.99"


def test_empty_url_stays_empty():
    assert _apply_proxy_port("", 8080) == ""


def test_path_preserved():
    assert _apply_proxy_port("http://host/base", 8080) == "http://host:8080/base"


class _FakeTeslaClient:
    """Ersetzt den echten HTTP-Client: liefert vorbereitete Antworten der Reihe nach."""

    def __init__(self, responses):
        self._responses = list(responses)

    async def vehicle_data(self, endpoints: str = "charge_state") -> dict:
        item = self._responses.pop(0)
        if item is TimeoutError:
            raise TimeoutError("Fahrzeug nicht erreichbar")
        return item


def _charge_response(charging_state: str) -> dict:
    return {"charge_state": {"charging_state": charging_state, "charger_actual_current": 0,
                             "charger_voltage": 230, "charger_phases": 1, "charger_power": 0}}


async def test_unreachable_vehicle_never_seen_connected_stays_idle():
    # Auto war noch nie als verbunden bestätigt (z. B. gar nicht angesteckt) –
    # ein BLE-Timeout darf es dann nicht plötzlich als "verbunden" ausgeben,
    # sonst reserviert die Prioritätskette Budget für ein Phantom-Fahrzeug.
    drv = TeslaVehicle({"backend": "ble_proxy", "proxy_url": "http://x"})
    drv.client = _FakeTeslaClient([TimeoutError])
    data = await drv.read_data()
    assert data.state == WallboxState.IDLE


async def test_unreachable_vehicle_last_seen_connected_stays_connected():
    # Zuletzt bestätigt angesteckt (nicht ladend) und dann eingeschlafen:
    # weiterhin "verbunden" annehmen, damit ein Ladestart nach dem Aufwachen
    # nicht verpasst wird (ursprüngliche Absicht dieses Fallbacks).
    drv = TeslaVehicle({"backend": "ble_proxy", "proxy_url": "http://x"})
    drv.client = _FakeTeslaClient([_charge_response("Stopped"), TimeoutError])
    first = await drv.read_data()
    assert first.state == WallboxState.CONNECTED
    drv._invalidate()
    second = await drv.read_data()
    assert second.state == WallboxState.CONNECTED
