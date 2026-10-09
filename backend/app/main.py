"""MinePower Backend – FastAPI-App mit Control-Loop als Background-Task."""
from __future__ import annotations

import logging
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
    sessions,
    settings_api,
    statistics,
    status,
    system,
    vehicles,
)
from .config import get_settings
from .core import runtime
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
    registry.discover()
    log.info("Treiber registriert: %s", ", ".join(sorted(m.id for m in registry.all_metas())))

    await startup.init_db()
    # Vor allem anderen: Eine leere DB nach einem Stromausfall bekommt hier
    # ihre letzte lokale Sicherung zurück (siehe services/backup.py), bevor
    # Erststart-Logik sie fälschlich als 'neue Anlage" behandelt.
    await startup.maybe_restore_from_backup()
    await startup.ensure_jwt_secret()
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
    if loop.mqtt:
        await loop.mqtt.close()
    log.info("MinePower beendet")


app = FastAPI(
    title="MinePower",
    description="PV-Überschuss-Ladecontroller für E-Auto, Warmwasser und Hausbatterie",
    version=__version__,
    lifespan=lifespan,
)

cors = get_settings().cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors.split(",")] if cors != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router_module in (auth, drivers, devices, battery, settings_api, status, history,
                      sessions, events, vehicles, system, statistics):
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
        if full_path.startswith("api/"):
            raise HTTPException(404)
        candidate = (STATIC_DIR / full_path).resolve()
        if candidate.is_file() and str(candidate).startswith(str(STATIC_DIR.resolve())):
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html", headers=INDEX_HEADERS)
