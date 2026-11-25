"""Daikin über die Onecta-Cloud (offizielle Daikin-Europe-API).

Für alle Daikin-Geräte mit dem neueren WLAN-Adapter (BRP069C4x, ab etwa
2020), die nur noch über die Cloud erreichbar sind: Split-Klimaanlagen
(Perfera, Stylish, Emura …) und Daikin Altherma (Raumheizung und
Warmwasserspeicher).

Ablauf:

* Anmeldung: OpenID Connect mit eigener Client-ID und Client-Secret
  (kostenlos im Daikin Developer Portal) und einem Refresh-Token, das
  der Knopf 'Mit Daikin verbinden' im Geräteformular holt (api/oauth.py). Daikin gibt bei jeder
  Erneuerung ein **neues** Refresh-Token aus; MinePower merkt sich das
  jeweils aktuelle verschlüsselt im Datenverzeichnis (``oauth_store``).
  Token: ``https://idp.onecta.daikineurope.com/v1/oidc/token``.
* Lesen: ``GET /v1/gateway-devices`` liefert alle Geräte des Kontos mit
  ihren Management-Points (``climateControl``, ``domesticHotWaterTank``).
* Schreiben: ``PATCH /v1/gateway-devices/{id}/management-points/{mp}/
  characteristics/{name}`` mit ``{"value": …}`` und bei Sollwerten
  ``"path": "/operationModes/<modus>/setpoints/<sollwert>"``.

Bei Überschuss (Konfiguration):

* Ziel ``climate`` (Klimaanlage, Altherma-Raumheizung)
  - ``power``     einschalten mit Betriebsart und Solltemperatur
                  'Überschuss'; ohne Überschuss aus – aber nur, wenn
                  MinePower eingeschaltet hat.
  - ``setpoint``  nur die Solltemperatur verstellen (Heizen höher, Kühlen
                  tiefer), danach zurück.
  - ``powerful``  Powerful-Modus an/aus.
* Ziel ``dhw`` (Altherma-Warmwasserspeicher)
  - ``powerful``  Warmwasser-Boost (Powerful-Modus) an/aus.
  - ``setpoint``  Warmwasser-Soll anheben, danach zurück.

Kontingent: Daikin erlaubt je Client-Anwendung nur **200 Abfragen am
Tag** (20 je Minute). Deshalb:

* Mehrere MinePower-Geräte am selben Konto teilen sich eine Anmeldung und
  eine Abfrage (``OnectaAccount``) – eine Abfrage liefert alle Geräte.
* Gelesen wird höchstens alle ``poll_s`` Sekunden (Vorgabe 15 min).
* Nach einem Befehl gilt der neue Zustand sofort (die Cloud braucht ohnehin
  1–2 min), die nächste echte Abfrage frühestens 2 min später.
* Befehle gehen nur bei einer Änderung raus, nie im Regeltakt.
* Bei HTTP 429 pausiert der Zugang (``Retry-After``, sonst 30 min,
  verdoppelt bis 12 h) und stellt keine Anfragen, bis die Sperre abläuft.

Leistung: Die API liefert keine Momentanleistung. Ist das Gerät an
(Warmwasser: Boost aktiv oder Soll angehoben), gilt die eingestellte
Leistungsaufnahme. Experimentell: nie an echter Hardware.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .. import __version__
from . import oauth_store
from .base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    WaterHeaterData,
    WaterHeaterDriver,
)
from .heat_common import kind_field, num, rated_field, wants_on
from .http_util import HttpDevice
from .registry import register
from .validation import ConnectionProblem, DeviceRejected, checked, sanitized

TOKEN_URL = "https://idp.onecta.daikineurope.com/v1/oidc/token"
API = "https://api.onecta.daikineurope.com"
PROVIDER = "daikin_onecta"

POLL_MIN_S = 600.0
POLL_MAX_S = 3600.0
AFTER_COMMAND_S = 120.0
BLOCK_FIRST_S = 1800.0
BLOCK_MAX_S = 12 * 3600.0

MP_TYPE = {"climate": "climateControl", "dhw": "domesticHotWaterTank"}
MODE_NAMES = {"heating": "Heizen", "cooling": "Kühlen", "auto": "Automatik", "dry": "Entfeuchten",
              "fanOnly": "Lüften"}


def char_value(mp: dict, name: str) -> Any:
    entry = mp.get(name)
    return entry.get("value") if isinstance(entry, dict) else None


def nested(mp: dict, name: str, *path: str) -> dict | None:
    """Unterobjekt in einem Charakteristik-Wert, z. B.
    temperatureControl → operationModes → heating → setpoints → roomTemperature."""
    node: Any = char_value(mp, name)
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, dict) else None


# ---------------------------------------------------------------- Konto

class OnectaAccount:
    """Eine Anmeldung, ein Cache, ein Kontingent je (Client-ID, Token)."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.initial_token = refresh_token
        self.key = oauth_store.store_key(PROVIDER, client_id, refresh_token)
        self.refresh_token = oauth_store.load(self.key) or refresh_token
        self.http = HttpDevice(API, name="Daikin Onecta", timeout=20.0)
        self.users = 0
        self.poll_s = 900.0
        self._token: str | None = None
        self._expires = 0.0
        self._devices: list[dict] | None = None
        self._fetched_at = 0.0
        self._next_read = 0.0
        self._blocked_until = 0.0
        self._block_s = BLOCK_FIRST_S
        self._lock = asyncio.Lock()
        self.remaining_day: int | None = None

    # ------------------------------------------------------------ Anmeldung

    async def _login(self) -> None:
        if not self.client_id or not self.client_secret or not self.refresh_token:
            raise DeviceRejected("Daikin Onecta: Client-ID, Client-Secret und Refresh-Token eintragen")
        try:
            res = await self.http.request("POST", TOKEN_URL, data={
                "grant_type": "refresh_token", "client_id": self.client_id,
                "client_secret": self.client_secret, "refresh_token": self.refresh_token})
        except DeviceRejected as exc:
            raise DeviceRejected(
                "Daikin Onecta: Anmeldung abgelehnt – Refresh-Token abgelaufen oder schon verbraucht "
                "(es gilt nur in einer Anwendung). Im Geräteformular 'Mit Daikin verbinden' "
                "klicken und speichern.") from exc
        if not isinstance(res, dict) or not res.get("access_token"):
            raise DeviceRejected("Daikin Onecta: Anmeldung ohne Zugriffstoken beantwortet")
        self._token = str(res["access_token"])
        new = res.get("refresh_token")
        if new and new != self.refresh_token:
            self.refresh_token = str(new)
            oauth_store.save(self.key, self.refresh_token)
        self._expires = time.monotonic() + max(60.0, (num(res.get("expires_in"), 3600.0) or 3600.0) - 120.0)

    def blocked(self) -> float:
        """Restsekunden der Sperre nach HTTP 429, sonst 0."""
        return max(0.0, self._blocked_until - time.monotonic())

    async def call(self, method: str, path: str, *, json: Any = None) -> Any:
        wait = self.blocked()
        if wait:
            raise ConnectionProblem(
                f"Daikin Onecta: Tageskontingent erschöpft – nächste Anfrage in {wait / 60:.0f} min")
        for attempt in (1, 2):
            if not self._token or time.monotonic() > self._expires:
                await self._login()
            headers = {"Authorization": f"Bearer {self._token}", "User-Agent": f"MinePower/{__version__}"}
            try:
                res = await self.http.request(method, path, json=json, headers=headers,
                                              want="json" if method == "GET" else "none")
            except DeviceRejected as exc:
                self._note_limits()
                if "HTTP 429" in str(exc):
                    self._block()
                    raise ConnectionProblem(
                        f"Daikin Onecta: Kontingent erschöpft (200 Anfragen am Tag) – Pause "
                        f"{self.blocked() / 60:.0f} min. Abfrageabstand erhöhen.") from exc
                if ("HTTP 401" not in str(exc) and "(HTTP 401)" not in str(exc)) or attempt == 2:
                    raise
                self._token = None
                continue
            self._note_limits()
            self._block_s = BLOCK_FIRST_S
            return res
        return None

    def _note_limits(self) -> None:
        headers = {k.lower(): v for k, v in (getattr(self.http, "last_headers", None) or {}).items()}
        rest = num(headers.get("x-ratelimit-remaining-day"))
        if rest is not None:
            self.remaining_day = int(rest)

    def _block(self) -> None:
        headers = {k.lower(): v for k, v in (getattr(self.http, "last_headers", None) or {}).items()}
        retry = num(headers.get("retry-after"))
        span = min(BLOCK_MAX_S, retry) if retry and retry > 0 else self._block_s
        self._block_s = min(BLOCK_MAX_S, self._block_s * 2)
        self._blocked_until = time.monotonic() + span

    # ------------------------------------------------------------ Lesen

    async def devices(self, *, force: bool = False) -> list[dict]:
        async with self._lock:
            now = time.monotonic()
            due = self._devices is None or (now >= self._next_read and now - self._fetched_at >= self.poll_s)
            if (force and now >= self._next_read) or due:
                if self._devices is not None and self.blocked():
                    return self._devices  # in der Sperre: letzter Stand
                try:
                    res = await self.call("GET", "/v1/gateway-devices")
                except ConnectionProblem:
                    if self._devices is not None and self.blocked():
                        return self._devices  # gerade gesperrt worden: letzter Stand
                    raise
                if not isinstance(res, list):
                    raise ConnectionProblem("Daikin Onecta: unerwartete Antwort der Geräteliste")
                self._devices = [d for d in res if isinstance(d, dict)]
                self._fetched_at = time.monotonic()
                self._next_read = 0.0
            return self._devices or []

    def after_command(self) -> None:
        """Die Cloud braucht nach einem Befehl 1–2 min – vorher nicht lesen."""
        self._next_read = time.monotonic() + AFTER_COMMAND_S
        self._fetched_at = min(self._fetched_at, time.monotonic() - self.poll_s + AFTER_COMMAND_S)

    async def patch(self, device_id: str, mp_id: str, name: str, value: Any, path: str | None = None) -> None:
        body: dict[str, Any] = {"value": value}
        if path:
            body["path"] = path
        await self.call("PATCH", f"/v1/gateway-devices/{device_id}/management-points/{mp_id}"
                                 f"/characteristics/{name}", json=body)
        self.after_command()


_accounts: dict[str, OnectaAccount] = {}


def account_for(client_id: str, client_secret: str, refresh_token: str) -> OnectaAccount:
    key = oauth_store.store_key(PROVIDER, client_id, refresh_token)
    acc = _accounts.get(key)
    if acc is None or acc.client_secret != client_secret:
        acc = OnectaAccount(client_id, client_secret, refresh_token)
        _accounts[key] = acc
    return acc


# ---------------------------------------------------------------- Treiber

@register
class DaikinOnecta(WaterHeaterDriver):
    meta = DriverMeta(
        id="daikin_onecta", name="Daikin Onecta (Klimaanlage, Altherma – Cloud)",
        category=DeviceCategory.WATER_HEATER,
        description="Daikin-Geräte mit Onecta-App (WLAN-Adapter BRP069C4x, Altherma) über die offizielle "
                    "Daikin-Cloud: bei Überschuss einschalten, Sollwert verstellen oder Powerful/Warmwasser-Boost.",
        capabilities={"relay", "climate", "write_test"},
        notes="Braucht Internet, Client-ID und Client-Secret aus dem Daikin Developer Portal "
              "(developer.cloud.daikineurope.com); das Token holt der Knopf 'Mit Daikin verbinden'. "
              "Daikin erlaubt nur 200 Anfragen am Tag – Abfrageabstand nicht unter 10 min. Mehrere Geräte "
              "am selben Konto teilen sich die Abfrage.",
        fields=[
            ConfigField(key="client_id", label="Client-ID (Developer Portal)"),
            ConfigField(key="client_secret", label="Client-Secret", type=FieldType.PASSWORD),
            ConfigField(key="refresh_token", label="Refresh-Token", type=FieldType.PASSWORD,
                        help="Füllt der Knopf 'Mit Daikin verbinden' aus. MinePower erneuert es danach selbst."),
            ConfigField(key="device_id", label="Geräte-ID", required=False,
                        help="Leer = erstes passendes Gerät des Kontos. Die IDs zeigt 'Verbindung testen'."),
            ConfigField(key="target", label="Was steuern", type=FieldType.SELECT, default="climate",
                        options=[{"value": "climate", "label": "Klimaanlage / Raumheizung"},
                                 {"value": "dhw", "label": "Warmwasserspeicher (Altherma)"}]),
            ConfigField(key="action", label="Bei Überschuss", type=FieldType.SELECT, default="power",
                        options=[{"value": "power", "label": "Einschalten (nur Klima/Raum)"},
                                 {"value": "setpoint", "label": "Solltemperatur verstellen"},
                                 {"value": "powerful", "label": "Powerful-Modus / Warmwasser-Boost"}]),
            ConfigField(key="surplus_mode", label="Betriebsart bei Überschuss", type=FieldType.SELECT,
                        default="heating", required=False,
                        options=[{"value": "heating", "label": "Heizen"}, {"value": "cooling", "label": "Kühlen"},
                                 {"value": "auto", "label": "Automatik"}],
                        help="Nur Klima/Raum. Beim Verstellen des Sollwerts gilt die laufende Betriebsart."),
            ConfigField(key="surplus_temp", label="Solltemperatur bei Überschuss (°C)", type=FieldType.NUMBER,
                        required=False, default=23,
                        help="Raum (Heizen höher, Kühlen tiefer) bzw. Warmwasser, z. B. 55."),
            ConfigField(key="poll_s", label="Abfrageabstand (s)", type=FieldType.NUMBER, default=900,
                        help="600–3600 s. 900 s = 96 Abfragen am Tag, Rest bleibt für Befehle."),
            rated_field(1000),
            kind_field("climate"),
        ],
    )

    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.client_id = str(config.get("client_id") or "").strip()
        self.client_secret = str(config.get("client_secret") or "").strip()
        self.initial_token = str(config.get("refresh_token") or "").strip()
        self.device_id = str(config.get("device_id") or "").strip()
        self.target = "dhw" if config.get("target") == "dhw" else "climate"
        action = str(config.get("action") or "power")
        if action not in ("power", "setpoint", "powerful"):
            action = "power"
        if self.target == "dhw" and action == "power":
            action = "powerful"  # Der Speicher ist immer 'an'; Einschalten hieße nichts.
        self.action = action
        mode = str(config.get("surplus_mode") or "heating")
        self.surplus_mode = mode if mode in ("heating", "cooling", "auto") else "heating"
        default_temp = 55.0 if self.target == "dhw" else 23.0
        self.surplus_temp = num(config.get("surplus_temp"), default_temp) or default_temp
        self.poll_s = max(POLL_MIN_S, min(POLL_MAX_S, num(config.get("poll_s"), 900.0) or 900.0))
        self.rated_power = num(config.get("rated_power"), 1000.0) or 1000.0
        kind = str(config.get("heat_kind") or "")
        if self.target == "dhw" and kind in ("", "climate"):
            kind = "heat_pump"
        self.heat_kind = kind or "climate"
        self.account = account_for(self.client_id, self.client_secret, self.initial_token)
        self._device: dict | None = None
        self._mp: dict | None = None
        self._released = False
        self._switched_on = False
        self._saved: tuple[str, float] | None = None  # (path, alter Sollwert)
        self._override: dict[str, Any] = {}

    @property
    def http(self) -> HttpDevice:  # für Tests austauschbar
        return self.account.http

    @http.setter
    def http(self, value: Any) -> None:
        self.account.http = value

    async def connect(self) -> None:
        self.account.users += 1
        self.account.poll_s = min(self.poll_s, self.account.poll_s if self.account.users > 1 else self.poll_s)
        await self.account.http.connect()

    async def disconnect(self) -> None:
        self.account.users = max(0, self.account.users - 1)
        if self.account.users == 0:
            await self.account.http.close()

    # ------------------------------------------------------------ Gerät finden

    def _pick(self, devices: list[dict]) -> tuple[dict, dict]:
        wanted = MP_TYPE[self.target]
        candidates = [d for d in devices if not self.device_id or str(d.get("id")) == self.device_id]
        if self.device_id and not candidates:
            raise DeviceRejected(f"Daikin Onecta: Gerät {self.device_id} nicht im Konto")
        for dev in candidates:
            for mp in dev.get("managementPoints") or []:
                if isinstance(mp, dict) and mp.get("managementPointType") == wanted:
                    return dev, mp
        what = "Warmwasserspeicher" if self.target == "dhw" else "Klima-/Raumsteuerung"
        raise DeviceRejected(f"Daikin Onecta: kein Gerät mit {what} gefunden"
                             + (" (Geräte-ID prüfen)" if self.device_id else ""))

    def _setpoint(self, mp: dict, mode: str | None = None) -> tuple[str, dict] | None:
        """(PATCH-Pfad, Sollwert-Objekt) für die Betriebsart."""
        if self.target == "dhw":
            mode, name = "heating", "domesticHotWaterTemperature"
        else:
            mode = mode or str(char_value(mp, "operationMode") or "heating")
            name = "roomTemperature"
        sp = nested(mp, "temperatureControl", "operationModes", mode, "setpoints", name)
        if sp is None:
            return None
        return f"/operationModes/{mode}/setpoints/{name}", sp

    # ------------------------------------------------------------ Lesen

    async def _refresh(self, *, force: bool = False) -> None:
        devices = await self.account.devices(force=force)
        self._device, self._mp = self._pick(devices)
        if not force:
            return
        self._override.clear()

    def _view(self) -> dict[str, Any]:
        """Aktueller Zustand: Cloud-Stand plus eigene, noch nicht gelesene Befehle."""
        mp = self._mp or {}
        on = char_value(mp, "onOffMode") == "on"
        mode = str(char_value(mp, "operationMode") or "")
        powerful = bool(char_value(mp, "isPowerfulModeActive") or char_value(mp, "powerfulMode") == "on")
        sp = self._setpoint(mp)
        target = num(sp[1].get("value")) if sp else None
        view = {"on": on, "mode": mode, "powerful": powerful, "target": target}
        view.update(self._override)
        return view

    async def read_data(self) -> WaterHeaterData:
        before = self.account._fetched_at
        await self._refresh()
        if self.account._fetched_at != before:
            self._override.clear()  # frischer Cloud-Stand ersetzt die Annahmen
        mp = self._mp or {}
        v = self._view()
        sensors = char_value(mp, "sensoryData") or {}
        def sensor(name: str) -> float | None:
            entry = sensors.get(name) if isinstance(sensors, dict) else None
            return num(entry.get("value")) if isinstance(entry, dict) else None
        temp = sensor("tankTemperature") if self.target == "dhw" else sensor("roomTemperature")
        if self.target == "dhw":
            running = v["powerful"] if self.action == "powerful" else self._released
        else:
            running = v["on"]
        extra: dict[str, Any] = {}
        if self._device is not None:
            extra["Gerät"] = f"{self._device.get('deviceModel') or '?'} ({self._device.get('id')})"
            if self._device.get("isCloudConnectionUp") is False:
                extra["Cloud"] = "Gerät offline"
        if self.target == "climate":
            extra["Zustand"] = "an" if v["on"] else "aus"
            if v["mode"]:
                extra["Betriebsart"] = MODE_NAMES.get(v["mode"], v["mode"])
        if v["powerful"]:
            extra["Powerful"] = "aktiv"
        if v["target"] is not None:
            extra["Soll"] = f"{v['target']:.1f} °C"
        outdoor = sensor("outdoorTemperature")
        if outdoor is not None:
            extra["Außen"] = f"{outdoor:.1f} °C"
        if self.account.remaining_day is not None:
            extra["Anfragen heute übrig"] = self.account.remaining_day
        is_on = self._released or (self.target == "dhw" and self.action == "powerful" and v["powerful"])
        if self.target == "climate" and self.action == "power":
            is_on = v["on"]
        return WaterHeaterData(
            power=checked("heater_power", self.rated_power if running else 0.0, source="Daikin Onecta"),
            is_on=bool(is_on), temperature_c=sanitized("water_temperature", temp), extra=extra)

    # ------------------------------------------------------------ Steuern

    async def _patch(self, name: str, value: Any, path: str | None = None) -> None:
        assert self._device is not None and self._mp is not None
        mp_id = str(self._mp.get("embeddedId") or MP_TYPE[self.target])
        await self.account.patch(str(self._device.get("id")), mp_id, name, value, path)

    def _settable(self, name: str) -> bool:
        entry = (self._mp or {}).get(name)
        return isinstance(entry, dict) and entry.get("settable", True) is not False

    async def set_power(self, watts: float) -> None:
        if self._mp is None:
            await self._refresh()
        on = wants_on(watts, self.rated_power)
        if on == self._released:
            return  # nichts zu tun – Befehle nur bei Änderung (Kontingent)
        v = self._view()
        if self.action == "powerful":
            await self._set_powerful(on, v)
        elif self.action == "setpoint":
            await self._set_setpoint(on, v)
        else:
            await self._set_power(on, v)
        self._released = on

    async def _set_powerful(self, on: bool, v: dict) -> None:
        if not self._settable("powerfulMode"):
            raise DeviceRejected("Daikin Onecta: Dieses Gerät bietet keinen Powerful-Modus an – "
                                 "'Solltemperatur verstellen' wählen")
        if on and not v["powerful"]:
            await self._patch("powerfulMode", "on")
            self._switched_on = True
            self._override["powerful"] = True
        elif not on and self._switched_on:
            if v["powerful"]:
                await self._patch("powerfulMode", "off")
            self._switched_on = False
            self._override["powerful"] = False

    async def _set_setpoint(self, on: bool, v: dict) -> None:
        sp = self._setpoint(self._mp or {})
        if sp is None or sp[1].get("settable") is False:
            raise DeviceRejected("Daikin Onecta: Solltemperatur ist in der aktuellen Betriebsart nicht "
                                 "einstellbar (z. B. witterungsgeführt) – andere Aktion wählen")
        path, entry = sp
        if on:
            want = self._clamp(self.surplus_temp, entry)
            current = v["target"]
            if current is None or abs(current - want) > 0.2:
                if self._saved is None and current is not None:
                    self._saved = (path, current)
                await self._patch("temperatureControl", want, path)
                self._override["target"] = want
        elif self._saved is not None:
            old_path, old = self._saved
            await self._patch("temperatureControl", old, old_path)
            self._override["target"] = old
            self._saved = None

    async def _set_power(self, on: bool, v: dict) -> None:
        if on:
            if v["on"]:
                return  # läuft schon (vom Nutzer) – nicht übernehmen, später nicht ausschalten
            if v["mode"] != self.surplus_mode and self._settable("operationMode"):
                await self._patch("operationMode", self.surplus_mode)
                self._override["mode"] = self.surplus_mode
            sp = self._setpoint(self._mp or {}, self.surplus_mode)
            if sp is not None and sp[1].get("settable") is not False:
                await self._patch("temperatureControl", self._clamp(self.surplus_temp, sp[1]), sp[0])
            await self._patch("onOffMode", "on")
            self._switched_on = True
            self._override["on"] = True
        elif self._switched_on:
            if v["on"]:
                await self._patch("onOffMode", "off")
            self._switched_on = False
            self._override["on"] = False

    @staticmethod
    def _clamp(value: float, entry: dict) -> float:
        lo, hi = num(entry.get("minValue")), num(entry.get("maxValue"))
        step = num(entry.get("stepValue"), 0.5) or 0.5
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        value = round(value / step) * step
        return int(value) if float(value).is_integer() else round(value, 1)

    async def test_command(self) -> dict | None:
        await self._refresh(force=True)
        devices = await self.account.devices()
        listing = ", ".join(f"{d.get('deviceModel') or '?'} = {d.get('id')}" for d in devices)
        ok = {"powerful": self._settable("powerfulMode"),
              "setpoint": (sp := self._setpoint(self._mp or {})) is not None and sp[1].get("settable") is not False,
              "power": self._settable("onOffMode")}[self.action]
        return {"ok": ok,
                "message": (f"Anmeldung und Abfrage erfolgreich. Geräte: {listing}"
                            + ("" if ok else " – die gewählte Aktion bietet dieses Gerät nicht an")),
                "sent": "nur gelesen",
                "readback": f"{self.account.remaining_day} Anfragen heute übrig"
                if self.account.remaining_day is not None else "–"}
