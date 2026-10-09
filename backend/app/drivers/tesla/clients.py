"""Tesla-Anbindung: zwei austauschbare Backends.

Methode A – TeslaBleHttpProxy (empfohlen, cloud-frei):
  Wir betreiben den Proxy NICHT selbst. Der Nutzer hat ihn bereits laufen
  (typisch: Raspberry Pi in BLE-Reichweite, https://github.com/wimaha/TeslaBleHttpProxy).
  Diese App spricht nur dessen lokale HTTP-API an (Fleet-API-kompatible Pfade):
    POST /api/1/vehicles/{vin}/command/{command}
    GET  /api/1/vehicles/{vin}/vehicle_data
  Key-Enrollment (Schlüssel-Kopplung mit dem Fahrzeug) erfolgt einmalig im
  Proxy selbst – nicht in dieser App.

Methode B – Tesla Fleet-API (Cloud):
  Benötigt OAuth-Token, ist rate-limitiert (konservativ pollen!), liefert
  dafür zuverlässig SoC/Telemetrie. Hinweis: Neuere Fahrzeuge verlangen
  signierte Kommandos (Vehicle-Command-Protokoll) – dann einen
  Fleet-API-kompatiblen Signier-Proxy als Basis-URL eintragen.
"""
from __future__ import annotations

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=15)


def unwrap_vehicle_data(data: dict) -> dict:
    """Normalisiert die vehicle_data-Antwort auf die innere Nutzlast.

    TeslaBleHttpProxy verschachtelt die Fleet-API-Antwort ein weiteres Mal:
      {"response": {"result": true, "vin": …, "response": {"charge_state": …}}}
    Die Fleet-API direkt liefert nur eine Ebene:
      {"response": {"charge_state": …}}
    Beide Formen ergeben hier {"charge_state": …, …}.
    """
    resp_obj = (data or {}).get("response") or {}
    if isinstance(resp_obj, dict) and isinstance(resp_obj.get("response"), dict):
        resp_obj = resp_obj["response"]
    return resp_obj


#: Ablehnungsgründe, die Tesla im Klartext zurückgibt → verständliche Meldung
REJECT_HINTS = {
    "is_charging": "Das Fahrzeug lädt bereits.",
    "not_charging": "Das Fahrzeug lädt gerade nicht.",
    "complete": "Das Ladeziel ist erreicht – das Fahrzeug nimmt keine Ladung mehr an.",
    "disconnected": "Kein Ladekabel verbunden.",
    "requested": "Der Befehl wurde bereits angefordert und läuft noch.",
    "invalid_command": "Dieses Fahrzeug unterstützt den Befehl nicht (Fahrzeug-Command-Protokoll?).",
    "unauthorized": "Nicht autorisiert – beim BLE-Proxy fehlt vermutlich die Schlüssel-Kopplung "
                    "mit dem Fahrzeug, bei der Fleet-API ist das Token abgelaufen.",
    "vehicle unavailable": "Fahrzeug nicht erreichbar (schläft, außer BLE-Reichweite oder offline).",
}


class TeslaCommandRejected(IOError):
    """Tesla hat den Befehl entgegengenommen und ausdrücklich abgelehnt."""

    def __init__(self, reason: str) -> None:
        self.reason = reason or "unbekannt"
        hint = next((v for k, v in REJECT_HINTS.items() if k in self.reason.lower()), "")
        super().__init__(f"Tesla lehnt den Befehl ab: {self.reason}. {hint}".strip())


class TeslaHttpClient:
    """Gemeinsamer HTTP-Client – Proxy und Fleet-API teilen sich die Pfade.

    Eine dauerhafte Session je Fahrzeug: Der BLE-Proxy ist ein kleiner Dienst
    auf einem Raspberry Pi, dem tausende neue TCP-Verbindungen pro Tag nicht
    guttun.
    """

    def __init__(self, base_url: str, vehicle_tag: str, auth_header: str = "") -> None:
        self.base = base_url.rstrip("/")
        self.tag = vehicle_tag  # VIN (Proxy) oder Fahrzeug-ID (Fleet)
        self.headers = {"Authorization": auth_header} if auth_header else {}
        self._session: aiohttp.ClientSession | None = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers, timeout=TIMEOUT)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _check_config(self) -> None:
        if not self.base:
            raise ConnectionError(
                "Keine Proxy-Adresse bzw. Fleet-Basis-URL konfiguriert."
            )
        if not self.tag:
            raise ConnectionError(
                "Keine Fahrzeugkennung konfiguriert (VIN beim BLE-Proxy, Fahrzeug-ID bei der Fleet-API)."
            )

    async def vehicle_data(self, endpoints: str = "charge_state") -> dict:
        self._check_config()
        url = f"{self.base}/api/1/vehicles/{self.tag}/vehicle_data"
        try:
            async with self._http().get(url, params={"endpoints": endpoints}) as resp:
                # 408 = Fahrzeug schläft. 503/504 = der BLE-Proxy erreicht es
                # gerade nicht (außer Reichweite, Funk belegt, Proxy neu
                # gestartet). Beides heißt 'jetzt keine Auskunft", nicht
                # 'defekt" – sonst flattert das Gerät im Minutentakt zwischen
                # online und offline und fällt dabei aus der Regelung.
                if resp.status in (408, 502, 503, 504):
                    raise TimeoutError(f"Fahrzeug nicht erreichbar ({resp.status})")
                if resp.status in (401, 403):
                    raise ConnectionError(
                        "Tesla verweigert den Zugriff. BLE-Proxy: Schlüssel-Kopplung mit dem "
                        "Fahrzeug prüfen. Fleet-API: Access-Token abgelaufen."
                    )
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except aiohttp.ClientConnectorError as exc:
            await self.close()
            raise ConnectionError(
                f"Tesla-Endpunkt {self.base} nicht erreichbar – läuft der TeslaBleHttpProxy "
                f"und stimmt der Port? ({exc})"
            ) from exc
        return unwrap_vehicle_data(data)

    async def command(self, command: str, payload: dict | None = None) -> dict:
        self._check_config()
        url = f"{self.base}/api/1/vehicles/{self.tag}/command/{command}"
        try:
            async with self._http().post(url, json=payload or {}) as resp:
                if resp.status in (401, 403):
                    raise TeslaCommandRejected("unauthorized")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except aiohttp.ClientConnectorError as exc:
            await self.close()
            raise ConnectionError(f"Tesla-Endpunkt {self.base} nicht erreichbar ({exc})") from exc
        result = data.get("response") or {}
        if isinstance(result, dict) and result.get("result") is False:
            raise TeslaCommandRejected(str(result.get("reason", "unbekannt")))
        return result

    async def wake_up(self) -> None:
        self._check_config()
        url = f"{self.base}/api/1/vehicles/{self.tag}/wake_up"
        async with self._http().post(url) as resp:
            resp.raise_for_status()


FLEET_BASE_URLS = {
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
}


def make_client(config: dict) -> TeslaHttpClient:
    backend = config.get("backend", "ble_proxy")
    if backend == "fleet":
        base = config.get("fleet_base_url") or FLEET_BASE_URLS.get(config.get("region", "eu"), FLEET_BASE_URLS["eu"])
        return TeslaHttpClient(base, str(config.get("vehicle_id", "")),
                               auth_header=f"Bearer {config.get('access_token', '')}")
    # BLE-Proxy: extern laufender TeslaBleHttpProxy.
    # Port getrennt konfigurierbar (Default 8080). Enthält proxy_url bereits
    # einen expliziten Port, hat dieser Vorrang; sonst wird proxy_port angehängt.
    base = _apply_proxy_port(str(config.get("proxy_url", "")), config.get("proxy_port"))
    return TeslaHttpClient(base, str(config.get("vin", "")),
                           auth_header=str(config.get("proxy_auth", "")))


def _apply_proxy_port(url: str, port) -> str:
    from urllib.parse import urlparse, urlunparse

    url = url.strip()
    if not url:
        return url
    if "://" not in url:
        url = "http://" + url
    parsed = urlparse(url)
    if parsed.port is not None or not port:  # URL bringt schon einen Port mit
        return url
    host = parsed.hostname or ""
    netloc = f"{host}:{int(port)}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
