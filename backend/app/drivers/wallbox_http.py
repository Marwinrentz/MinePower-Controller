"""Wallboxen mit lokaler HTTP-Schnittstelle.

Hardy Barth (eCB1 und Salia), SMA EV Charger, EVSE-WiFi, openWB Pro und
ein generischer Treiber für jede Box mit JSON-Status und Steuer-URLs.
Alle Treiber sind experimentell (nicht an Hardware geprüft).
"""
from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Any

from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WallboxData, WallboxDriver, WallboxState
from .generic_common import extract, flag, number, parse_payload, path_field
from .http_util import HttpDevice
from .registry import register
from .validation import DeviceRejected, DriverError
from .wallbox_common import MAX_CURRENT_FIELD, PHASES_FIELD, Heartbeat, f, host_field, iec_state, phases_of, wallbox_data

WALLBOX = DeviceCategory.WALLBOX
CAPS = {"write_test"}


def ci(data: Any, *keys: str) -> Any:
    """Verschachtelter Zugriff ohne Rücksicht auf Groß-/Kleinschreibung."""
    node = data
    for key in keys:
        if not isinstance(node, dict):
            return None
        low = key.lower()
        node = next((v for k, v in node.items() if str(k).lower() == low), None)
    return node


class _HttpWallbox(Heartbeat, WallboxDriver):
    http: HttpDevice

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.max_current = f(config.get("max_current"), 16.0) or 16.0
        self.phases = phases_of(config)
        self._amps = self.min_current

    def clamp(self, amps: float) -> float:
        return max(self.min_current, min(float(amps), self.max_current))

    async def connect(self) -> None:
        await self.http.connect()
        self.start_heartbeat()

    async def disconnect(self) -> None:
        self.stop_heartbeat()
        await self.http.close()


def _base(config: dict, default_scheme: str = "http") -> str:
    host = str(config.get("host", "")).strip().rstrip("/")
    return host if "://" in host else f"{default_scheme}://{host}"


# ---------------------------------------------------------------- Hardy Barth eCB1

@register
class HardyBarthEcb1(_HttpWallbox):
    meta = DriverMeta(
        id="hardybarth_ecb1", name="Hardy Barth cPH1/cPH2 mit eCB1 (HTTP)", category=WALLBOX,
        description="Hardy Barth Wallboxen mit eCB1-Controller über die lokale REST-API.",
        capabilities=CAPS,
        notes="Die Box wird beim ersten Stellbefehl in den Modus 'manual' gesetzt.",
        fields=[host_field("192.0.2.21"),
                ConfigField(key="chargecontrol", label="Ladepunkt", type=FieldType.NUMBER, default=1),
                ConfigField(key="meter", label="Zähler", type=FieldType.NUMBER, default=1),
                MAX_CURRENT_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(_base(config) + "/api/v1", name="Hardy Barth eCB1")
        self.cc = int(f(config.get("chargecontrol"), 1) or 1)
        self.meter = int(f(config.get("meter"), 1) or 1)
        self._manual = False

    async def _ensure_manual(self) -> None:
        if not self._manual:
            await self.http.request("POST", f"/chargecontrols/{self.cc}/mode", data={"mode": "manual"}, want="none")
            self._manual = True

    async def read_data(self) -> WallboxData:
        cc = ci(await self.http.get_json(f"/chargecontrols/{self.cc}"), "chargecontrol") or {}
        if not ci(cc, "state"):
            raise DriverError("Hardy Barth: kein Zustand. Controller-Typ prüfen (eCB1 oder Salia).")
        state_id = int(f(ci(cc, "stateid")))
        if not ci(cc, "connected"):
            state = WallboxState.IDLE
        elif state_id == 5:
            state = WallboxState.CHARGING
        else:
            state = WallboxState.CONNECTED
        data = ci(await self.http.get_json(f"/meters/{self.meter}"), "meter", "data") or {}
        power = f(data.get("1-0:1.4.0"))
        currents = [f(data.get(k)) for k in ("1-0:31.4.0", "1-0:51.4.0", "1-0:71.4.0")]
        enabled = state_id != 17
        return wallbox_data(state, power, current_set=f(ci(cc, "manualmodeamp")) if enabled else 0.0,
                            currents=currents, extra={"Modus": ci(cc, "mode") or "?"}, source="eCB1 1-0:1.4.0")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self._ensure_manual()
        await self.http.request("POST", f"/chargecontrols/{self.cc}/mode/manual/ampere",
                                data={"manualmodeamp": str(int(self.clamp(amps)))}, want="none")

    async def start_charging(self) -> None:
        await self._ensure_manual()
        await self.http.request("POST", f"/chargecontrols/{self.cc}/start", json={}, want="none")

    async def stop_charging(self) -> None:
        await self._ensure_manual()
        await self.http.request("POST", f"/chargecontrols/{self.cc}/stop", json={}, want="none")


# ---------------------------------------------------------------- Hardy Barth Salia

def _version(text: str) -> tuple[int, ...]:
    out = []
    for part in str(text or "0").lstrip("v").split("-")[0].split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


@register
class HardyBarthSalia(_HttpWallbox):
    meta = DriverMeta(
        id="hardybarth_salia", name="Hardy Barth mit Salia-Controller (HTTP)", category=WALLBOX,
        description="Hardy Barth cPH2 und Salia PLCC über die lokale API.",
        capabilities=CAPS,
        notes="Die Box wird beim ersten Stellbefehl in den Modus 'manual' gesetzt.",
        fields=[host_field("192.0.2.22"),
                ConfigField(key="username", label="Benutzer", required=False),
                ConfigField(key="password", label="Passwort", type=FieldType.PASSWORD, required=False),
                MAX_CURRENT_FIELD],
    )
    heartbeat_s = 30.0

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        headers = None
        if config.get("username") and config.get("password"):
            token = base64.b64encode(f"{config['username']}:{config['password']}".encode()).decode()
            headers = {"Authorization": f"Basic {token}"}
        self.root = _base(config)
        self.http = HttpDevice(self.root, headers=headers, name="Hardy Barth Salia")
        self._fw: tuple[int, ...] | None = None
        self._manual = False

    async def _api(self) -> dict:
        data = await self.http.get_json("/api")
        if not isinstance(data, dict):
            raise DriverError("Salia: Antwort ohne JSON-Objekt")
        self._fw = _version(ci(data, "device", "software_version") or "0")
        return data

    async def _post(self, key: str, value: str) -> None:
        if self._fw is None:
            await self._api()
        if self._fw >= (2, 3, 64):
            res = await self.http.request("POST", "/save_mqtt.php", json={key: value})
        else:
            res = await self.http.request("PUT", "/api/secc", json={key: value})
        if isinstance(res, dict) and str(ci(res, "result")).lower() not in ("ok", "none"):
            raise DeviceRejected(f"Salia: {key} abgelehnt ({ci(res, 'result')})")

    async def heartbeat(self) -> None:
        await self._post("salia/heartbeat", "alive")

    async def _ensure_manual(self) -> None:
        if not self._manual:
            await self._post("salia/chargemode", "manual")
            self._manual = True

    async def read_data(self) -> WallboxData:
        port = ci(await self._api(), "secc", "port0") or {}
        state = iec_state(str(ci(port, "ci", "charge", "cp", "status") or ""))
        power = f(ci(port, "metering", "power", "active_total", "actual"))
        if (self._fw or (0,)) < (2,):
            limit = f(ci(port, "grid_current_limit"))
        else:
            limit = f(ci(port, "ci", "evse", "basic", "offered_current_limit"))
        paused = int(f(ci(port, "salia", "pausecharging"))) == 1
        return wallbox_data(state, power, current_set=0.0 if paused else limit,
                            extra={"Modus": ci(port, "salia", "chargemode") or "?"}, source="Salia active_total")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self._ensure_manual()
        self._amps = self.clamp(amps)
        await self._post("grid_current_limit", str(int(self._amps)))

    async def start_charging(self) -> None:
        await self._ensure_manual()
        await self._post("grid_current_limit", str(int(self._amps)))
        await self._post("salia/pausecharging", "0")

    async def stop_charging(self) -> None:
        await self._ensure_manual()
        await self._post("salia/pausecharging", "1")


# ---------------------------------------------------------------- SMA EV Charger

@register
class SmaEvCharger(_HttpWallbox):
    meta = DriverMeta(
        id="sma_evcharger", name="SMA EV Charger 7.4/22 (HTTP)", category=WALLBOX,
        description="SMA EV Charger über die lokale Web-API (Firmware ab 1.2.23).",
        capabilities=CAPS,
        notes="Drehschalter an der Box auf 'Schnellladen'. App-Sperre im Gerät deaktivieren.",
        fields=[host_field("192.0.2.23"),
                ConfigField(key="scheme", label="Protokoll", type=FieldType.SELECT, default="http",
                            options=[{"value": "http", "label": "HTTP"}, {"value": "https", "label": "HTTPS"}]),
                ConfigField(key="username", label="Benutzer"),
                ConfigField(key="password", label="Passwort", type=FieldType.PASSWORD),
                MAX_CURRENT_FIELD],
    )
    COMPONENT = "IGULD:SELF"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        scheme = str(config.get("scheme") or "http")
        self.http = HttpDevice(_base(config, scheme) + "/api/v1", name="SMA EV Charger",
                               verify_ssl=scheme != "https")
        self._token: str | None = None
        self._expires = 0.0

    async def _auth(self) -> dict[str, str]:
        if not self._token or time.monotonic() > self._expires:
            res = await self.http.request("POST", "/token", data={
                "grant_type": "password", "username": str(self.config.get("username") or ""),
                "password": str(self.config.get("password") or "")})
            if not isinstance(res, dict) or not res.get("access_token"):
                raise DeviceRejected("SMA EV Charger: Anmeldung fehlgeschlagen")
            self._token = res["access_token"]
            self._expires = time.monotonic() + max(60.0, f(res.get("expires_in"), 600) - 60)
        return {"Authorization": f"Bearer {self._token}"}

    async def _call(self, method: str, path: str, body: Any) -> Any:
        try:
            return await self.http.request(method, path, json=body, headers=await self._auth())
        except DeviceRejected:
            self._token = None  # abgelaufen: einmal neu anmelden
            return await self.http.request(method, path, json=body, headers=await self._auth())

    async def _measurements(self) -> dict[str, float]:
        res = await self._call("POST", "/measurements/live", [{"componentId": self.COMPONENT}]) or []
        out = {}
        for item in res:
            values = item.get("values") or []
            if values:
                out[str(item.get("channelId"))] = f(values[0].get("value"))
        return out

    async def _parameters(self) -> dict[str, str]:
        res = await self._call("POST", "/parameters/search/", {"queryItems": [{"componentId": self.COMPONENT}]}) or []
        values = (res[0].get("values") if res else None) or []
        return {str(v.get("channelId")): str(v.get("value")) for v in values}

    async def _send(self, channel: str, value: str) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        await self._call("PUT", f"/parameters/{self.COMPONENT}/",
                         {"values": [{"timestamp": stamp, "channelId": channel, "value": value}]})

    async def read_data(self) -> WallboxData:
        m = await self._measurements()
        p = await self._parameters()
        code = int(m.get("Measurement.Operation.EVeh.ChaStt", 0))
        state = {200111: WallboxState.IDLE, 200112: WallboxState.CONNECTED,
                 200113: WallboxState.CHARGING}.get(code, WallboxState.ERROR)
        power = m.get("Measurement.Metering.GridMs.TotWIn", m.get("Measurement.Metering.GridMs.TotWIn.ChaSta", 0.0))
        currents = [abs(m.get(f"Measurement.GridMs.A.phs{x}", 0.0)) for x in "ABC"]
        session = m.get("Measurement.ChaSess.WhIn")
        enabled = p.get("Parameter.Chrg.ActChaMod") in ("4718", "4719", "4720")
        return wallbox_data(state, power, current_set=f(p.get("Parameter.Inverter.AcALim")) if enabled else 0.0,
                            currents=currents, energy_session_kwh=session / 1000.0 if session is not None else None,
                            source="SMA GridMs.TotWIn")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self._send("Parameter.Inverter.AcALim", f"{self.clamp(amps):.2f}")

    async def start_charging(self) -> None:
        await self._send("Parameter.Chrg.ActChaMod", "4718")

    async def stop_charging(self) -> None:
        await self._send("Parameter.Chrg.ActChaMod", "4721")


# ---------------------------------------------------------------- EVSE-WiFi

@register
class EvseWifi(_HttpWallbox):
    meta = DriverMeta(
        id="evse_wifi", name="EVSE-WiFi / SimpleEVSE (HTTP)", category=WALLBOX,
        description="SimpleEVSE mit EVSE-WiFi-Modul über die lokale HTTP-API.",
        capabilities=CAPS,
        notes="In EVSE-WiFi 'Always Active' ausschalten, sonst ist Sperren nicht möglich.",
        fields=[host_field("192.0.2.24"), MAX_CURRENT_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(_base(config), name="EVSE-WiFi")

    async def _ok(self, path: str, params: dict) -> None:
        text = str(await self.http.get_text(path, params) or "")
        if not text.startswith("S0_"):
            raise DeviceRejected(f"EVSE-WiFi: {text.strip()[:80] or 'keine Bestätigung'}")

    async def read_data(self) -> WallboxData:
        res = await self.http.get_json("/getParameters")
        items = (res or {}).get("list") or []
        if not items:
            raise DriverError("EVSE-WiFi: leere Parameterliste")
        p = items[0]
        state = {1: WallboxState.IDLE, 2: WallboxState.CONNECTED, 3: WallboxState.CHARGING}.get(
            int(f(p.get("vehicleState"))), WallboxState.ERROR)
        currents = [f(p.get(f"currentP{i}")) for i in (1, 2, 3)]
        return wallbox_data(state, f(p.get("actualPower")) * 1000.0,
                            current_set=f(p.get("actualCurrent")) if p.get("evseState") else 0.0,
                            currents=currents if any(currents) else None, voltage=f(p.get("voltageP1")) or None,
                            energy_session_kwh=f(p.get("energy")), source="EVSE-WiFi actualPower")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self._ok("/setCurrent", {"current": int(self.clamp(amps))})

    async def start_charging(self) -> None:
        await self._ok("/setStatus", {"active": "true"})

    async def stop_charging(self) -> None:
        await self._ok("/setStatus", {"active": "false"})


# ---------------------------------------------------------------- openWB Pro

@register
class OpenWbPro(_HttpWallbox):
    meta = DriverMeta(
        id="openwb_pro", name="openWB Pro (HTTP)", category=WALLBOX,
        description="openWB Pro bzw. Pro+ im Modus 'externe Steuerung' über die lokale HTTP-API.",
        capabilities=CAPS | {"phase_switch"},
        notes="In der openWB Pro unter Einstellungen die Steuerung durch ein externes System zulassen.",
        fields=[host_field("192.0.2.25"), MAX_CURRENT_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(_base(config), name="openWB Pro")

    async def _set(self, **values: Any) -> None:
        await self.http.request("POST", "/connect.php", data={k: str(v) for k, v in values.items()}, want="none")

    async def read_data(self) -> WallboxData:
        s = await self.http.get_json("/connect.php")
        if not isinstance(s, dict):
            raise DriverError("openWB Pro: Antwort ohne JSON-Objekt")
        if s.get("charge_state"):
            state = WallboxState.CHARGING
        elif s.get("plug_state"):
            state = WallboxState.CONNECTED
        else:
            state = WallboxState.IDLE
        currents = [f(c) for c in (s.get("currents") or [])][:3]
        voltages = [f(v) for v in (s.get("voltages") or [])]
        soc = f(s.get("soc_value"), -1)
        return wallbox_data(state, f(s.get("power_all")), current_set=f(s.get("offered_current")),
                            currents=currents, voltage=voltages[0] if voltages else None,
                            soc=soc if 0 < soc <= 100 else None, source="openWB Pro power_all")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        self._amps = self.clamp(amps)
        await self._set(ampere=f"{self._amps:.1f}")

    async def start_charging(self) -> None:
        await self._set(ampere=f"{self._amps:.1f}")

    async def stop_charging(self) -> None:
        await self._set(ampere="0")

    async def set_phases(self, phases: int) -> None:
        await self._set(phasetarget=1 if phases < 2 else 3)


# ---------------------------------------------------------------- generisch

@register
class HttpWallbox(_HttpWallbox):
    meta = DriverMeta(
        id="http_wallbox", name="Wallbox über HTTP (generisch)", category=WALLBOX,
        description="Status als JSON von einer URL, Steuerung über URLs mit Platzhalter {current}.",
        capabilities=CAPS,
        fields=[
            ConfigField(key="status_url", label="URL Status", placeholder="http://192.0.2.26/status"),
            path_field("power_path", "Pfad Ladeleistung (W)", placeholder="power"),
            path_field("plugged_path", "Pfad Fahrzeug verbunden", required=False, placeholder="plugged"),
            path_field("current_path", "Pfad Ladestrom (A)", required=False),
            path_field("soc_path", "Pfad Fahrzeug-Ladestand", required=False),
            ConfigField(key="current_url", label="URL Ladestrom",
                        placeholder="http://192.0.2.26/set?current={current}"),
            ConfigField(key="enable_url", label="URL Laden ein", placeholder="http://192.0.2.26/set?enable=1"),
            ConfigField(key="disable_url", label="URL Laden aus", placeholder="http://192.0.2.26/set?enable=0"),
            MAX_CURRENT_FIELD, PHASES_FIELD,
        ],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice("", name="HTTP-Wallbox")

    async def read_data(self) -> WallboxData:
        data = parse_payload(await self.http.get_text(str(self.config.get("status_url") or "")))
        power = number(extract(data, self.config.get("power_path")), what="Ladeleistung")

        def opt(key: str) -> Any:
            return extract(data, self.config[key]) if self.config.get(key) else None

        plugged = opt("plugged_path")
        current = opt("current_path")
        soc = opt("soc_path")
        if power > 100:
            state = WallboxState.CHARGING
        elif plugged is not None and not flag(plugged):
            state = WallboxState.IDLE
        else:
            state = WallboxState.CONNECTED
        return wallbox_data(state, power, current_set=number(current) if current is not None else None,
                            soc=number(soc) if soc is not None else None, source="HTTP")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        url = str(self.config.get("current_url") or "").replace("{current}", str(int(self.clamp(amps))))
        await self.http.get_text(url)

    async def start_charging(self) -> None:
        await self.http.get_text(str(self.config.get("enable_url") or ""))

    async def stop_charging(self) -> None:
        await self.http.get_text(str(self.config.get("disable_url") or ""))
