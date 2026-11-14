"""Zugangsdaten nie ins Protokoll.

Ein Filter für alle Log-Ausgaben: Schlüssel in Adressen (``/mcp/k/<Schlüssel>``,
``?token=…``), Bearer-Tokens und Telegram-Bot-Tokens werden durch ``***``
ersetzt, bevor die Zeile geschrieben wird. Das betrifft vor allem das
Zugriffsprotokoll von uvicorn, das jede Adresse samt Query ausgibt, und
Fehlermeldungen von HTTP-Bibliotheken, die die Adresse wiederholen.
"""
from __future__ import annotations

import logging
import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/mcp/k/[^/?\s\"']+"), "/mcp/k/***"),
    (re.compile(r"([?&](?:token|access_token|refresh_token|api_key|apikey|key|password|pw)=)[^&\s\"']+", re.I),
     r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1***"),
    (re.compile(r"(api\.telegram\.org/bot)[^/\s\"']+"), r"\1***"),
    # Zugangsdaten in Adressen: schema://nutzer:passwort@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@\"']+):[^@/\s\"']+@", re.I), r"\1:***@"),
)


def redact(text: str) -> str:
    """Bekannte Stellen mit Zugangsdaten in einem Text schwärzen."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_clean(a) for a in args)
        elif isinstance(args, dict):
            record.args = {k: _clean(v) for k, v in args.items()}
        return True


def _clean(value):
    if isinstance(value, str):
        return redact(value)
    # Ausnahmen werden mit %s formatiert; ihr Text kann eine Adresse enthalten.
    if isinstance(value, BaseException):
        return redact(str(value))
    return value


_FILTER = RedactFilter()


def install() -> None:
    """Filter an den Zugriffs- und Fehlerprotokollen von uvicorn und an allen
    Handlern des Wurzel-Loggers anbringen. Mehrfach aufrufbar."""
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
    for handler in logging.getLogger().handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)


install()
