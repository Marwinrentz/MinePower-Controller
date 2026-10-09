"""Gemeinsame HTTP-Hilfen für alle netzwerkbasierten Treiber (aiohttp).

Was hier zentral gelöst ist, muss kein Treiber mehr selbst tun:

* **Eine dauerhafte Session je Gerät** statt einer neuen pro Anfrage.
  Bei 3-Sekunden-Takt bedeutet 'Session pro Request' tausende TCP-Handshakes
  pro Stunde – kleine Wallboxen und Shellys quittieren das irgendwann mit
  Verbindungsabbrüchen. Mit Keep-alive bleibt eine Verbindung offen.
* **Reconnect**: Ist die Session nach einem Netzwerkaussetzer kaputt, wird
  sie verworfen und beim nächsten Zugriff neu aufgebaut.
* **Klartext-Fehler statt Rohausnahme** – aiohttp-Ausnahmen sagen dem Nutzer
  nichts; hier werden sie in konkrete Prüfschritte übersetzt.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .validation import ConnectionProblem, DeviceRejected

log = logging.getLogger(__name__)


class HttpDevice:
    """Persistenter HTTP-Client für ein Gerät im lokalen Netz."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 8.0,
        verify_ssl: bool = True,
        headers: dict[str, str] | None = None,
        name: str = "Gerät",
    ) -> None:
        self.base = base_url.rstrip("/")
        self.name = name
        self.verify_ssl = verify_ssl
        self.headers = headers or {}
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------ Lebenszyklus

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=None if self.verify_ssl else False, limit=4)
            self._session = aiohttp.ClientSession(
                timeout=self._timeout, connector=connector, headers=self.headers
            )
        return self._session

    async def connect(self) -> None:
        self._ensure_session()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _reset(self) -> None:
        await self.close()

    # ------------------------------------------------------------ Anfragen

    async def get_json(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params, want="json")

    async def get_text(self, path: str, params: dict | None = None) -> str:
        return await self._request("GET", path, params=params, want="text")

    async def post_json(self, path: str, payload: dict | None = None, params: dict | None = None) -> Any:
        return await self._request("POST", path, params=params, json=payload, want="json")

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None,
                      data: dict | str | None = None, headers: dict[str, str] | None = None,
                      want: str = "json") -> Any:
        """Beliebige Methode mit JSON- oder Formular-Körper und eigenen Kopfzeilen.
        `want`: 'json', 'text' oder 'none' (Antwort verwerfen)."""
        return await self._request(method, path, params=params, json=json, data=data, headers=headers, want=want)

    async def _request(
        self, method: str, path: str, *, params=None, json=None, data=None, headers=None, want: str = "json",
        _retry: bool = True,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        if not self.base and not path.startswith("http"):
            raise ConnectionProblem(f"{self.name}: keine Adresse konfiguriert")
        session = self._ensure_session()
        try:
            async with session.request(
                method, url, params=_stringify(params), json=json, data=data, headers=headers
            ) as resp:
                await self._check_status(resp, url)
                if want == "text":
                    return await resp.text()
                if want == "none":
                    return None
                if resp.content_length == 0:
                    return None
                # Viele Geräte senden JSON mit falschem Content-Type
                return await resp.json(content_type=None)
        except aiohttp.ClientResponseError:
            raise
        except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, ConnectionResetError) as exc:
            if _retry:
                log.debug("%s: Verbindung zu %s verloren (%s) – neuer Versuch", self.name, url, exc)
                await self._reset()
                return await self._request(method, path, params=params, json=json, data=data, headers=headers,
                                           want=want, _retry=False)
            raise ConnectionProblem(
                f"{self.name}: {url} nicht erreichbar. IP-Adresse, Port und Netzwerk prüfen – "
                f"bei WLAN-Geräten auch, ob das Gerät gerade erreichbar ist."
            ) from exc
        except asyncio.TimeoutError as exc:
            raise ConnectionProblem(
                f"{self.name}: Zeitüberschreitung bei {url}. Gerät antwortet nicht rechtzeitig "
                f"(überlastet, im Neustart oder Firmware-Update?)."
            ) from exc
        except aiohttp.ClientError as exc:
            raise ConnectionProblem(f"{self.name}: HTTP-Fehler bei {url} – {exc}") from exc

    async def _check_status(self, resp: aiohttp.ClientResponse, url: str) -> None:
        if resp.status < 400:
            return
        body = ""
        try:
            body = (await resp.text())[:200]
        except Exception:  # noqa: BLE001
            pass
        if resp.status in (401, 403):
            raise DeviceRejected(
                f"{self.name}: Zugriff abgelehnt (HTTP {resp.status}). Zugangsdaten bzw. "
                f"Authentifizierung im Gerät prüfen."
            )
        if resp.status == 404:
            raise DeviceRejected(
                f"{self.name}: Endpunkt {url} existiert nicht (HTTP 404). Meist ist die "
                f"Geräte-Generation falsch gewählt oder die lokale API im Gerät nicht aktiviert."
            )
        if resp.status == 408:
            raise TimeoutError(f"{self.name}: Gerät meldet Zeitüberschreitung (HTTP 408)")
        raise DeviceRejected(f"{self.name}: HTTP {resp.status} von {url}{f' – {body}' if body else ''}")


def _stringify(params: dict | None) -> dict[str, str] | None:
    """aiohttp akzeptiert nur str/int in Query-Parametern – bools sauber abbilden."""
    if not params:
        return None
    out: dict[str, str] = {}
    for key, value in params.items():
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out
