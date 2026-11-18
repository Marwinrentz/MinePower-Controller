"""Die letzten Protokollzeilen im Speicher – für die Diagnose in der
Oberfläche und den Diagnose-Export.

Ein Handler am Wurzel-Logger hält die jüngsten Zeilen in einem Ringpuffer.
Zugangsdaten sind zu diesem Zeitpunkt schon geschwärzt (core/logsafe.py);
der Zugriffslog von uvicorn (eine Zeile je Anfrage) bleibt draußen.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from itertools import count

from .logsafe import redact

CAPACITY = 2000
LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

_records: deque[dict] = deque(maxlen=CAPACITY)
_ids = count(1)


class BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("uvicorn.access"):
            return
        try:
            message = redact(record.getMessage())
            # Verbindungsauf- und -abbau der Live-Ansicht: eine Zeile je Tab
            # und Neuverbindung, ohne Aussage.
            if record.name == "uvicorn.error" and (
                    message.startswith("connection ") or '"WebSocket ' in message):
                return
            if record.exc_info and record.exc_info[1] is not None:
                message += f" – {type(record.exc_info[1]).__name__}: {redact(str(record.exc_info[1]))}"
            _records.append({
                "id": next(_ids),
                "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": message[:2000],
            })
        except Exception:  # noqa: BLE001 – Protokollieren darf nie selbst scheitern
            pass


_handler = BufferHandler(level=logging.INFO)


def install() -> None:
    root = logging.getLogger()
    if _handler not in root.handlers:
        root.addHandler(_handler)
    # uvicorn.error hängt nicht am Wurzel-Logger
    uv = logging.getLogger("uvicorn.error")
    if _handler not in uv.handlers:
        uv.addHandler(_handler)


def entries(*, level: str = "info", query: str = "", limit: int = 200, after_id: int = 0) -> list[dict]:
    """Jüngste Zeilen (älteste zuerst), gefiltert nach Mindeststufe und Text."""
    minimum = LEVELS.get(level.lower(), logging.INFO)
    needle = query.strip().lower()
    out = [r for r in _records
           if r["id"] > after_id and LEVELS.get(r["level"], logging.CRITICAL) >= minimum
           and (not needle or needle in r["message"].lower() or needle in r["logger"].lower())]
    return out[-max(1, min(limit, CAPACITY)):]


def clear() -> None:
    _records.clear()
