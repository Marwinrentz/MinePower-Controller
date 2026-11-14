"""MinePower Backend – FastAPI-App mit Control-Loop als Background-Task."""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, startup
from .api import (
    auth,
    battery,
    devices,
    drivers,
    events,
    history,
    mcp,
    sessions,
    settings_api,
    statistics,
    status,
    system,
    vehicles,
)
from .config import get_settings
from .core import logsafe, runtime
from .core.loop import ControlLoop
from .drivers import registry
from .services.backup import BackupService
from .services.forecast import ForecastService
from .services.mqtt import MqttPublisher
from .services.notifications import Notifier
from .services.tariff import TariffService

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Zugangsdaten in Adressen und Fehlermeldungen schwärzen (core/logsafe.py)
    logsafe.install()
    registry.discover()
    log.info("Treiber registriert: %s", ", ".join(sorted(m.id for m in registry.all_metas())))

    await startup.wait_for_db()
    await startup.init_db()
    # Vor allem anderen: Eine leere DB nach einem Stromausfall bekommt hier
    # ihre letzte lokale Sicherung zurück (siehe services/backup.py), bevor
    # Erststart-Logik sie fälschlich als 'neue Anlage" behandelt.
    await startup.maybe_restore_from_backup()
    await startup.ensure_jwt_secret()
    if len(cfg.jwt_secret.encode()) < 32:
        log.warning("JWT_SECRET ist kürzer als 32 Zeichen – für HS256 zu schwach. "
                    "Längeren Wert setzen oder die Variable leer lassen (wird dann erzeugt).")
    await startup.seed_defaults()
    await startup.migrate_settings()
    from .core import limits, secrets
    await secrets.migrate()
    await limits.enforce_on_startup()
    await startup.track_version()
    await startup.import_headless_config()
    if cfg.demo_mode:
        await startup.setup_demo_mode()

    loop = ControlLoop(default_interval_s=cfg.control_interval_s)
    loop.tariff = TariffService()
    loop.forecast = ForecastService()
    loop.notifier = Notifier()
    if cfg.mqtt_url:
        loop.mqtt = MqttPublisher(cfg.mqtt_url, cfg.mqtt_base_topic)
    runtime.loop = loop
    backup_service = BackupService()

    loop.tariff.start()
    loop.forecast.start()
    loop.start()
    backup_service.start()
    log.info("MinePower gestartet")

    yield

    # Den Loop zuerst anhalten: Er setzt einen Speicher im Zwangsmodus auf
    # Eigenverbrauch zurück (Fail-safe), und Docker gibt beim Stoppen nur
    # wenige Sekunden Zeit, bevor es hart beendet.
    await loop.stop()
    await loop.tariff.stop()
    await loop.forecast.stop()
    await backup_service.stop()
    await loop.notifier.close()
    if loop.mqtt:
        await loop.mqtt.close()
    log.info("MinePower beendet")


_cfg = get_settings()
app = FastAPI(
    title="MinePower",
    description="PV-Überschuss-Ladecontroller für E-Auto, Warmwasser und Hausbatterie",
    version=__version__,
    lifespan=lifespan,
    # Die API-Beschreibung ist ohne Anmeldung lesbar – nur auf Wunsch (API_DOCS=true).
    docs_url="/docs" if _cfg.api_docs else None,
    redoc_url="/redoc" if _cfg.api_docs else None,
    openapi_url="/openapi.json" if _cfg.api_docs else None,
)

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-_]+(:\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](:\d{1,5})?$")
#: Seiten der API-Dokumentation laden Skripte von einem CDN – dort gilt die
#: strenge Richtlinie nicht (nur mit API_DOCS=true erreichbar).
_DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    """Sicherheits-Kopfzeilen für jede HTTP-Antwort.

    * ``Content-Security-Policy``: Skripte, Stile, Bilder und Verbindungen nur
      vom eigenen Ursprung. Eingeschleuster Code (XSS) kann damit weder
      nachladen noch das Anmelde-Token an fremde Server schicken.
    * ``X-Frame-Options`` / ``frame-ancestors``: keine Einbettung in fremde
      Seiten (Clickjacking auf Schaltflächen wie 'Sofort laden').
    * ``X-Content-Type-Options``, ``Referrer-Policy``, ``Permissions-Policy``.
    * ``Cache-Control: no-store`` für API-Antworten: Anlagendaten bleiben
      nicht in Zwischenspeichern liegen.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        host = ""
        for key, value in scope.get("headers", []):
            if key == b"host":
                host = value.decode("latin-1")
                break

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}

                def add(name: bytes, value: str) -> None:
                    if name not in present:
                        headers.append((name, value.encode("latin-1")))

                add(b"x-content-type-options", "nosniff")
                add(b"x-frame-options", "DENY")
                add(b"referrer-policy", "no-referrer")
                add(b"permissions-policy",
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()")
                add(b"cross-origin-opener-policy", "same-origin")
                if not path.startswith(_DOCS_PATHS):
                    add(b"content-security-policy", content_security_policy(host))
                if path.startswith(("/api/", "/mcp")):
                    add(b"cache-control", "no-store")
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def content_security_policy(host: str = "") -> str:
    # WebSocket zum eigenen Host ausdrücklich: Ältere Browser zählen ws:// bei
    # 'self' nicht mit. Der Host stammt aus der Anfrage und wird nur in
    # geprüfter Form übernommen.
    ws = f" ws://{host} wss://{host}" if host and _HOST_RE.match(host) else ""
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        # Stile: React und die Diagramme setzen Inline-Stile (style-Attribute).
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        f"connect-src 'self'{ws}; "
        "worker-src 'self'; manifest-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )


app.add_middleware(SecurityHeadersMiddleware)

cors = _cfg.cors_origins.strip()
if cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if cors == "*" else [o.strip() for o in cors.split(",") if o.strip()],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

for router_module in (auth, drivers, devices, battery, settings_api, status, history,
                      sessions, events, vehicles, system, statistics, mcp):
    app.include_router(router_module.router)


# ---------------------------------------------------------------- Frontend
# Im All-in-One-Image liegt der React-Build unter /app/static und wird direkt
# von FastAPI serviert (SPA-Fallback auf index.html). In der Entwicklung
# (vite dev-server) existiert das Verzeichnis nicht – dann nur API.
from pathlib import Path  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

if STATIC_DIR.is_dir():
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    #: index.html nie ungeprüft aus dem Cache: Nach einem Update zeigte der
    #: Browser (oder die als App installierte Seite am Handy) sonst noch die
    #: alte Oberfläche, obwohl das Backend längst neu war – neue Knöpfe
    #: 'fehlten". Mit no-cache fragt der Browser jedes Mal kurz nach (ETag,
    #: meist 304); die Dateien unter /assets tragen ihren Inhalt im Namen und
    #: dürfen dagegen lange gecacht werden.
    INDEX_HEADERS = {"Cache-Control": "no-cache"}

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=INDEX_HEADERS)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        # API, MCP und OAuth-Erkundung (KI-Konnektoren) nie mit index.html beantworten
        if full_path.startswith(("api/", "mcp", ".well-known/")):
            raise HTTPException(404)
        # Nur Dateien unterhalb des Oberflächen-Ordners. Vergleich über die
        # Pfadteile, nicht über den Textanfang ('/app/static-x' zählt nicht).
        candidate = (STATIC_DIR / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html", headers=INDEX_HEADERS)
