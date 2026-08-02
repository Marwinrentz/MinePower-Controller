"""Unit-Tests für Treiber-Hilfslogik (ohne Hardware)."""
from app.drivers.tesla.clients import _apply_proxy_port, unwrap_vehicle_data


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
