"""Wallboxen, die nur über die offizielle Hersteller-Cloud steuerbar sind.

Easee (api.easee.com) und Zaptec (api.zaptec.com) veröffentlichen ihre
REST-APIs für Drittsysteme. Beide begrenzen die Zahl der Anfragen: Der
Zustand wird deshalb höchstens alle ``poll_s`` Sekunden abgefragt, dazwischen
gilt der zuletzt gelesene Wert. Stellbefehle wirken mit einigen Sekunden
Verzögerung. Ohne Internet ist keine Regelung möglich. Experimentell.
"""
from __future__ import annotations

import time
from typing import Any

from .. import __version__
from .base import ConfigField, DeviceCategory, DriverMeta, FieldType, WallboxData, WallboxDriver, WallboxState
from .http_util import HttpDevice
from .registry import register
from .validation import ConnectionProblem, DeviceRejected, DriverError
from .wallbox_common import MAX_CURRENT_FIELD, f, wallbox_data

WALLBOX = DeviceCategory.WALLBOX
POLL_FIELD = ConfigField(key="poll_s", label="Abfrageabstand (s)", type=FieldType.NUMBER, default=30,
                         help="15–300 s, Cloud-Limit beachten")


class _CloudWallbox(WallboxDriver):
    http: HttpDevice

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.max_current = f(config.get("max_current"), 16.0) or 16.0
        self.poll_s = max(15.0, min(300.0, f(config.get("poll_s"), 30.0) or 30.0))
        self._token: str | None = None
        self._expires = 0.0
        self._cache: WallboxData | None = None
        self._cached_at = 0.0

    def clamp(self, amps: float) -> float:
        return max(self.min_current, min(float(amps), self.max_current))

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def login(self) -> tuple[str, float]:
        raise NotImplementedError

    async def _headers(self) -> dict[str, str]:
        if not self._token or time.monotonic() > self._expires:
            self._token, lifetime = await self.login()
            self._expires = time.monotonic() + max(60.0, lifetime - 120.0)
        return {"Authorization": f"Bearer {self._token}", "User-Agent": f"MinePower/{__version__}"}

    async def call(self, method: str, path: str, *, json: Any = None, want: str = "json") -> Any:
        try:
            return await self.http.request(method, path, json=json, headers=await self._headers(), want=want)
        except DeviceRejected as exc:
            if "401" not in str(exc):
                raise
            self._token = None  # Token abgelaufen: einmal neu anmelden
            return await self.http.request(method, path, json=json, headers=await self._headers(), want=want)

    async def fetch(self) -> WallboxData:
        raise NotImplementedError

    async def read_data(self) -> WallboxData:
        if self._cache is None or time.monotonic() - self._cached_at >= self.poll_s:
            self._cache = await self.fetch()
            self._cached_at = time.monotonic()
        return self._cache

    def invalidate(self) -> None:
        self._cached_at = 0.0


# ---------------------------------------------------------------- Easee

EASEE_STATES = {1: WallboxState.IDLE, 2: WallboxState.CONNECTED, 3: WallboxState.CHARGING,
                4: WallboxState.COMPLETE, 5: WallboxState.ERROR, 6: WallboxState.CONNECTED,
                7: WallboxState.CONNECTED, 8: WallboxState.CONNECTED}


@register
class Easee(_CloudWallbox):
    meta = DriverMeta(
        id="easee", name="Easee Home/Charge (Cloud-API)", category=WALLBOX,
        description="Easee über die offizielle Easee-Cloud-API (api.easee.com).",
        capabilities={"write_test"},
        notes="Zugangsdaten wie in der Easee-App. Seriennummer der Box z. B. EH123456. Benötigt Internet.",
        offline_hint="Internetverbindung und Easee-Cloud-Status prüfen.",
        fields=[ConfigField(key="username", label="Benutzer (E-Mail oder Telefon)"),
                ConfigField(key="password", label="Passwort", type=FieldType.PASSWORD),
                ConfigField(key="charger_id", label="Seriennummer", placeholder="EH000000"),
                MAX_CURRENT_FIELD, POLL_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice("https://api.easee.com/api", name="Easee")
        self.charger = str(config.get("charger_id") or "").strip()

    async def login(self) -> tuple[str, float]:
        res = await self.http.request("POST", "/accounts/login", json={
            "userName": str(self.config.get("username") or ""), "password": str(self.config.get("password") or "")})
        if not isinstance(res, dict) or not res.get("accessToken"):
            raise DeviceRejected("Easee: Anmeldung fehlgeschlagen")
        return str(res["accessToken"]), f(res.get("expiresIn"), 3600)

    async def fetch(self) -> WallboxData:
        s = await self.call("GET", f"/chargers/{self.charger}/state")
        if not isinstance(s, dict):
            raise DriverError("Easee: Antwort ohne Zustand")
        mode = int(f(s.get("chargerOpMode")))
        if mode == 0:
            raise ConnectionProblem("Easee: Box offline (Cloud meldet keine Verbindung)")
        state = EASEE_STATES.get(mode, WallboxState.ERROR)
        currents = [f(s.get(k)) for k in ("inCurrentT3", "inCurrentT4", "inCurrentT5")]
        active = mode in (3, 6)
        return wallbox_data(state, f(s.get("totalPower")) * 1000.0,
                            current_set=f(s.get("dynamicChargerCurrent")) if active else 0.0,
                            currents=currents, voltage=f(s.get("voltage")) or None,
                            energy_session_kwh=f(s.get("sessionEnergy")), source="Easee totalPower")

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        await self.call("POST", f"/chargers/{self.charger}/settings",
                        json={"dynamicChargerCurrent": round(self.clamp(amps), 1)}, want="none")
        self.invalidate()

    async def start_charging(self) -> None:
        await self.call("POST", f"/chargers/{self.charger}/commands/resume_charging", want="none")
        self.invalidate()

    async def stop_charging(self) -> None:
        await self.call("POST", f"/chargers/{self.charger}/commands/pause_charging", want="none")
        self.invalidate()


# ---------------------------------------------------------------- Zaptec

ZAPTEC_STATES = {1: WallboxState.IDLE, 2: WallboxState.CONNECTED, 3: WallboxState.CHARGING,
                 5: WallboxState.COMPLETE}


@register
class Zaptec(_CloudWallbox):
    meta = DriverMeta(
        id="zaptec", name="Zaptec Go/Pro (Cloud-API)", category=WALLBOX,
        description="Zaptec Go, Go 2 und Pro über die offizielle Zaptec-Cloud-API (api.zaptec.com).",
        capabilities={"write_test"},
        notes="Zugangsdaten des Zaptec-Portals. Stand-alone-Modus in der Zaptec-App ausschalten. Benötigt Internet.",
        offline_hint="Internetverbindung und Zaptec-Portal prüfen.",
        fields=[ConfigField(key="username", label="Benutzer (E-Mail)"),
                ConfigField(key="password", label="Passwort", type=FieldType.PASSWORD),
                ConfigField(key="charger_id", label="Lader-ID", required=False,
                            help="Leer = erster Lader im Konto"),
                MAX_CURRENT_FIELD, POLL_FIELD],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice("https://api.zaptec.com", name="Zaptec")
        self.charger = str(config.get("charger_id") or "").strip()

    async def login(self) -> tuple[str, float]:
        res = await self.http.request("POST", "/oauth/token", data={
            "grant_type": "password", "username": str(self.config.get("username") or ""),
            "password": str(self.config.get("password") or "")})
        if not isinstance(res, dict) or not res.get("access_token"):
            raise DeviceRejected("Zaptec: Anmeldung fehlgeschlagen")
        return str(res["access_token"]), f(res.get("expires_in"), 3600)

    async def _charger_id(self) -> str:
        if not self.charger:
            res = await self.call("GET", "/api/chargers")
            items = (res or {}).get("Data") or (res or {}).get("data") or []
            if not items:
                raise DriverError("Zaptec: kein Lader im Konto")
            self.charger = str(items[0].get("Id") or items[0].get("id"))
        return self.charger

    async def fetch(self) -> WallboxData:
        cid = await self._charger_id()
        obs = {int(o.get("StateId", -1)): o.get("ValueAsString") for o in (await self.call(
            "GET", f"/api/chargers/{cid}/state") or [])}
        if str(obs.get(712, "")).lower() in ("1", "true"):
            raise DriverError("Zaptec: Stand-alone-Modus aktiv, Vorgaben werden ignoriert")
        state = ZAPTEC_STATES.get(int(f(obs.get(710))), WallboxState.ERROR)
        stopped = str(obs.get(718, "")).lower() in ("1", "true")
        currents = [f(obs.get(i)) for i in (507, 508, 509)]
        return wallbox_data(state, f(obs.get(513)), current_set=0.0 if stopped else f(obs.get(708)),
                            currents=currents, voltage=f(obs.get(501)) or None,
                            energy_session_kwh=f(obs.get(553)), source="Zaptec 513")

    async def _command(self, code: int) -> None:
        cid = await self._charger_id()
        try:
            await self.call("POST", f"/api/chargers/{cid}/sendCommand/{code}", want="none")
        except DeviceRejected as exc:
            # 520/528: Befehl ändert nichts am Zustand (schon gestoppt bzw. läuft schon)
            if "520" not in str(exc) and "528" not in str(exc):
                raise
        self.invalidate()

    async def set_current(self, amps: float) -> None:
        if amps <= 0:
            await self.stop_charging()
            return
        cid = await self._charger_id()
        await self.call("POST", f"/api/chargers/{cid}/update",
                        json={"maxChargeCurrent": round(self.clamp(amps), 1)}, want="none")
        self.invalidate()

    async def start_charging(self) -> None:
        await self._command(507)

    async def stop_charging(self) -> None:
        await self._command(506)
