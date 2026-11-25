"""Viessmann-Wärmepumpe über die offizielle Viessmann-API (ViCare-Cloud).

Ablauf:

* Anmeldung: OAuth2 mit eigener Client-ID (kostenlos im Viessmann Developer
  Portal anlegen, Redirect-URI ``http://localhost:4200/``) und einem
  Refresh-Token. Das Token holt der Knopf 'Mit Viessmann verbinden' (api/oauth.py)
  oder ``backend/tools/vicare_token.py``;
  es gilt 180 Tage. Daraus wird stündlich ein Zugriffstoken erzeugt
  (``https://iam.viessmann.com/idp/v3/token``).
* Gerät: Installation, Gateway-Seriennummer und Geräte-ID. Leer gelassen,
  nimmt MinePower die erste Installation des Kontos
  (``/iot/v1/equipment/installations?includeGateways=true``).
* Features: ``/iot/v2/features/installations/{i}/gateways/{g}/devices/{d}/features``

Bei Überschuss (Konfiguration):

* ``dhw_once`` – Warmwasser-Einmalladung (``heating.dhw.oneTimeCharge``)
                 starten, ohne Überschuss beenden. Das Gerät entscheidet,
                 ob Verdichter oder Heizstab heizt.
* ``dhw_temp`` – Warmwasser-Solltemperatur (``heating.dhw.temperature.main``)
                 auf den Überschusswert anheben, danach zurück.

Gelesen werden Warmwassertemperatur (``heating.dhw.sensors.temperature.
dhwCylinder``, ältere Geräte ``…hotWaterStorage``) und ob der Verdichter
läuft (``heating.compressors.0``). Leistung: Die API liefert keine
Momentanleistung – läuft der Verdichter, gilt die eingestellte
Leistungsaufnahme.

Kontingent: Das kostenlose Konto erlaubt rund 1450 Abfragen am Tag. Der
Zustand wird deshalb höchstens alle ``poll_s`` Sekunden (Vorgabe 120 s)
gelesen; dazwischen gilt der letzte Wert. Experimentell: nie an echter
Hardware.
"""
from __future__ import annotations

import time
from typing import Any

from .. import __version__
from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WaterHeaterData, WaterHeaterDriver
from .heat_common import kind_field, num, rated_field, wants_on
from .http_util import HttpDevice
from .registry import register
from .validation import ConnectionProblem, DeviceRejected, checked, sanitized

TOKEN_URL = "https://iam.viessmann.com/idp/v3/token"
API = "https://api.viessmann.com"
DHW_TEMP = ("heating.dhw.sensors.temperature.dhwCylinder", "heating.dhw.sensors.temperature.hotWaterStorage")
ONCE = "heating.dhw.oneTimeCharge"
DHW_TARGET = "heating.dhw.temperature.main"
COMPRESSOR = "heating.compressors.0"


def feature_value(features: dict[str, dict], name: str, prop: str = "value") -> Any:
    entry = features.get(name) or {}
    p = (entry.get("properties") or {}).get(prop) or {}
    return p.get("value")


@register
class ViessmannViCare(WaterHeaterDriver):
    meta = DriverMeta(
        id="viessmann_vicare", name="Viessmann Wärmepumpe (ViCare-API)", category=DeviceCategory.WATER_HEATER,
        description="Vitocal & Co. über die offizielle Viessmann-API: bei Überschuss Warmwasser-Einmalladung "
                    "oder höhere Warmwasser-Solltemperatur.",
        capabilities={"relay", "heat_pump", "write_test"},
        notes="Braucht Internet und eine eigene Client-ID aus dem Viessmann Developer Portal. Das Token holt der "
              "Knopf 'Mit Viessmann verbinden' (gilt 180 Tage). Rund 1450 Abfragen am Tag frei – "
              "Abfrageabstand nicht unter 90 s.",
        fields=[
            ConfigField(key="client_id", label="Client-ID (Developer Portal)"),
            ConfigField(key="refresh_token", label="Refresh-Token", type=FieldType.PASSWORD,
                        help="Füllt der Knopf 'Mit Viessmann verbinden' aus."),
            ConfigField(key="installation_id", label="Installations-ID", required=False,
                        help="Leer = erste Installation des Kontos"),
            ConfigField(key="gateway_serial", label="Gateway-Seriennummer", required=False,
                        help="Leer = erstes Gateway der Installation"),
            ConfigField(key="device_id", label="Geräte-ID", required=False, default="0"),
            ConfigField(key="action", label="Bei Überschuss", type=FieldType.SELECT, default="dhw_once",
                        options=[{"value": "dhw_once", "label": "Warmwasser-Einmalladung starten"},
                                 {"value": "dhw_temp", "label": "Warmwasser-Solltemperatur anheben"}]),
            ConfigField(key="surplus_temp", label="Warmwasser-Soll bei Überschuss (°C)", type=FieldType.NUMBER,
                        required=False, default=55, help="Nur für 'Solltemperatur anheben'."),
            ConfigField(key="poll_s", label="Abfrageabstand (s)", type=FieldType.NUMBER, default=120,
                        help="90–900 s, Tageskontingent beachten"),
            rated_field(2000),
            kind_field("heat_pump"),
        ],
    )

    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(API, name="Viessmann-API", timeout=15.0)
        self.client_id = str(config.get("client_id") or "").strip()
        self.refresh_token = str(config.get("refresh_token") or "").strip()
        self.installation = str(config.get("installation_id") or "").strip()
        self.gateway = str(config.get("gateway_serial") or "").strip()
        self.device = str(config.get("device_id") or "0").strip() or "0"
        self.action = "dhw_temp" if config.get("action") == "dhw_temp" else "dhw_once"
        self.surplus_temp = num(config.get("surplus_temp"), 55.0) or 55.0
        self.poll_s = max(90.0, min(900.0, num(config.get("poll_s"), 120.0) or 120.0))
        self.rated_power = num(config.get("rated_power"), 2000.0) or 2000.0
        self.heat_kind = str(config.get("heat_kind") or "heat_pump")
        self._token: str | None = None
        self._expires = 0.0
        self._cache: WaterHeaterData | None = None
        self._features: dict[str, dict] = {}
        self._cached_at = 0.0
        self._released = False
        self._saved_target: float | None = None

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    # ------------------------------------------------------------ Anmeldung

    async def _login(self) -> None:
        if not self.client_id or not self.refresh_token:
            raise DeviceRejected("Viessmann: Client-ID und Refresh-Token eintragen")
        res = await self.http.request("POST", TOKEN_URL, data={
            "grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": self.refresh_token})
        if not isinstance(res, dict) or not res.get("access_token"):
            raise DeviceRejected("Viessmann: Anmeldung abgelehnt – Refresh-Token abgelaufen? "
                                 "Im Geräteformular neu verbinden.")
        self._token = str(res["access_token"])
        if res.get("refresh_token"):
            self.refresh_token = str(res["refresh_token"])
        self._expires = time.monotonic() + max(60.0, (num(res.get("expires_in"), 3600.0) or 3600.0) - 120.0)

    async def _call(self, method: str, path: str, *, json: Any = None, want: str = "json") -> Any:
        for attempt in (1, 2):
            if not self._token or time.monotonic() > self._expires:
                await self._login()
            headers = {"Authorization": f"Bearer {self._token}", "User-Agent": f"MinePower/{__version__}"}
            try:
                return await self.http.request(method, path, json=json, headers=headers, want=want)
            except DeviceRejected as exc:
                if "401" not in str(exc) or attempt == 2:
                    raise
                self._token = None
        return None

    async def _discover(self) -> None:
        if self.installation and self.gateway:
            return
        res = await self._call("GET", "/iot/v1/equipment/installations?includeGateways=true")
        items = (res or {}).get("data") or []
        if not items:
            raise DeviceRejected("Viessmann: keine Installation im Konto gefunden")
        inst = next((i for i in items if str(i.get("id")) == self.installation), items[0])
        gateways = inst.get("gateways") or []
        if not gateways:
            raise DeviceRejected("Viessmann: Installation ohne Gateway")
        self.installation = str(inst.get("id"))
        self.gateway = self.gateway or str(gateways[0].get("serial"))

    def _base(self) -> str:
        return (f"/iot/v2/features/installations/{self.installation}/gateways/{self.gateway}"
                f"/devices/{self.device}/features")

    # ------------------------------------------------------------ Lesen

    async def _fetch(self) -> WaterHeaterData:
        await self._discover()
        res = await self._call("GET", self._base())
        data = (res or {}).get("data")
        if not isinstance(data, list):
            raise ConnectionProblem("Viessmann: unerwartete Antwort der Feature-Liste")
        self._features = {str(f.get("feature")): f for f in data if isinstance(f, dict)}
        feats = self._features
        temp = None
        for name in DHW_TEMP:
            temp = num(feature_value(feats, name))
            if temp is not None:
                break
        compressor = feature_value(feats, COMPRESSOR, "active")
        once = feature_value(feats, ONCE, "active")
        target = num(feature_value(feats, DHW_TARGET))
        if compressor is not None:
            running = bool(compressor)
        else:
            running = bool(once) if self.action == "dhw_once" else self._released
        extra: dict[str, Any] = {}
        if compressor is not None:
            extra["Verdichter"] = "läuft" if compressor else "aus"
        if once is not None:
            extra["Einmalladung"] = "aktiv" if once else "aus"
        if target is not None:
            extra["Warmwasser-Soll"] = f"{target:.0f} °C"
        on = bool(once) if self.action == "dhw_once" and once is not None else self._released
        return WaterHeaterData(power=checked("heater_power", self.rated_power if running else 0.0, source="Viessmann"),
                               is_on=on, temperature_c=sanitized("water_temperature", temp), extra=extra)

    async def read_data(self) -> WaterHeaterData:
        if self._cache is None or time.monotonic() - self._cached_at >= self.poll_s:
            self._cache = await self._fetch()
            self._cached_at = time.monotonic()
        return self._cache

    # ------------------------------------------------------------ Steuern

    async def _command(self, feature: str, command: str, body: dict) -> None:
        await self._call("POST", f"{self._base()}/{feature}/commands/{command}", json=body)

    async def set_power(self, watts: float) -> None:
        await self._discover()
        if not self._features:
            await self.read_data()
        on = wants_on(watts, self.rated_power)
        if self.action == "dhw_once":
            active = feature_value(self._features, ONCE, "active")
            if on and not active:
                await self._command(ONCE, "activate", {})
                self._released = True
            elif not on and self._released:
                if active:
                    await self._command(ONCE, "deactivate", {})
                self._released = False
            else:
                return
        else:
            target = num(feature_value(self._features, DHW_TARGET))
            if on:
                if target is None or abs(target - self.surplus_temp) > 0.4:
                    if self._saved_target is None:
                        self._saved_target = target
                    await self._command(DHW_TARGET, "setTargetTemperature",
                                        {"temperature": round(self.surplus_temp)})
                self._released = True
            elif self._released:
                if self._saved_target is not None:
                    await self._command(DHW_TARGET, "setTargetTemperature",
                                        {"temperature": round(self._saved_target)})
                self._saved_target = None
                self._released = False
            else:
                return
        # Nächste Abfrage holt den neuen Zustand
        self._cached_at = 0.0

    async def test_command(self) -> dict | None:
        data = await self._fetch()
        has = ONCE in self._features if self.action == "dhw_once" else DHW_TARGET in self._features
        return {"ok": has,
                "message": (f"Anmeldung und Abfrage erfolgreich, {len(self._features)} Datenpunkte"
                            + ("" if has else " – die gewählte Aktion bietet dieses Gerät nicht an")),
                "sent": "nur gelesen", "readback": data.extra.get("Verdichter", "–")}
