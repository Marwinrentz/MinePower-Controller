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
    assert _apply_proxy_port("http://192.0.2.99", 8080) == "http://192.0.2.99:8080"


def test_proxy_port_added_scheme_when_missing():
    assert _apply_proxy_port("192.0.2.99", 8080) == "http://192.0.2.99:8080"


def test_explicit_port_in_url_wins():
    assert _apply_proxy_port("http://192.0.2.99:9000", 8080) == "http://192.0.2.99:9000"


def test_no_port_config_keeps_url():
    # ohne proxy_port bleibt die URL unverändert (Port 80 implizit)
    assert _apply_proxy_port("http://192.0.2.99", None) == "http://192.0.2.99"


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


# --------------------------------------------- Ladeleistung: fein vor grob
# Aus dem Diagnosebericht: Das Fahrzeug lud einphasig mit 6 A – real 1380 W.
# Gemeldet wurde "charger_power": 1, also 1 kW. Mit diesem Wert im Budget
# fehlten 380 W in der Rechnung; in der Gegenrichtung (16 A, gemeldet 4 kW
# statt 3,68 kW) forderte der Regler 320 W mehr an, als die Sonne hergab –
# und die kamen aus Speicher oder Netz.

def _charging(**over) -> dict:
    charge = {"charging_state": "Charging", "charger_actual_current": 6,
              "charger_voltage": 230, "charger_phases": 1, "charger_power": 1,
              "charge_amps": 6}
    charge.update(over)
    return {"charge_state": charge}


async def _read(**over):
    drv = TeslaVehicle({"backend": "ble_proxy", "proxy_url": "http://x"})
    drv.client = _FakeTeslaClient([_charging(**over)])
    return await drv.read_data()


async def test_power_comes_from_current_and_voltage_not_coarse_kw():
    """6 A bei 230 V sind 1380 W – nicht die gemeldete eine Kilowattstunde."""
    data = await _read()
    assert data.power == 1380.0


async def test_coarse_kw_cannot_inflate_the_power():
    """Gegenrichtung: 16 A sind 3680 W, auch wenn 4 kW gemeldet werden."""
    data = await _read(charger_actual_current=16, charger_power=4, charge_amps=16)
    assert data.power == 3680.0


async def test_measured_voltage_is_used():
    """Reale Netzspannung statt Nennwert: 6 A bei 243 V sind 1458 W."""
    data = await _read(charger_voltage=243)
    assert data.power == 6 * 243.0
    assert data.voltage == 243.0


async def test_three_phase_reported_as_two_is_understood():
    """Tesla meldet in Europa `charger_phases` = 2 für dreiphasiges Laden.
    Ungeprüft übernommen fehlt ein Drittel der Leistung."""
    data = await _read(charger_actual_current=16, charger_phases=2, charger_power=11,
                       charge_amps=16)
    assert data.phases_active == 3
    assert data.power == 16 * 230.0 * 3


async def test_missing_phase_count_is_inferred_from_the_coarse_kw():
    """Fehlt die Phasenzahl, taugt der grobe kW-Wert genau für diese eine
    Frage: 11 kW passen zu dreiphasig, nicht zu einphasig."""
    data = await _read(charger_actual_current=16, charger_phases=0, charger_power=11,
                       charge_amps=16)
    assert data.phases_active == 3


async def test_single_phase_is_inferred_when_the_coarse_kw_is_small():
    data = await _read(charger_actual_current=16, charger_phases=0, charger_power=4,
                       charge_amps=16)
    assert data.phases_active == 1


async def test_power_falls_back_to_coarse_kw_without_a_current_reading():
    """Meldet das Fahrzeug keinen Strom, bleibt nur der grobe Wert."""
    data = await _read(charger_actual_current=0, charger_power=2)
    assert data.power == 2000.0


async def test_unreported_voltage_is_not_passed_off_as_a_measurement():
    """Ein stillschweigend eingesetzter Nennwert sähe für den Regler wie eine
    Messung aus und würde dessen Einschwingprüfung umgehen."""
    data = await _read(charger_voltage=0)
    assert data.voltage is None
    assert data.power == 6 * 230.0     # Nennwert nur für die Rechnung


# --------------------------------------------- Sollstrom wird abgerundet

class _RecordingClient(_FakeTeslaClient):
    def __init__(self):
        super().__init__([])
        self.sent = None

    async def command(self, name, payload=None):
        self.sent = (name, payload)
        return {}


async def test_set_current_rounds_down_never_up():
    """Aufrunden fordert ein Ampere mehr an, als der Regler zugeteilt hat –
    einphasig 230 W aus Speicher oder Netz."""
    drv = TeslaVehicle({"backend": "ble_proxy", "proxy_url": "http://x", "max_current": 16})
    drv.client = _RecordingClient()
    await drv.set_current(9.8)
    assert drv.client.sent[1]["charging_amps"] == 9


async def test_set_current_respects_the_vehicle_floor():
    """Unter 5 A lädt ein Tesla nicht – der Wert wird angehoben, nicht gesendet."""
    drv = TeslaVehicle({"backend": "ble_proxy", "proxy_url": "http://x", "max_current": 16})
    drv.client = _RecordingClient()
    await drv.set_current(3.0)
    assert drv.client.sent[1]["charging_amps"] == 5
