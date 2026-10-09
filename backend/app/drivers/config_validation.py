"""Prüfung der Treiber-Konfiguration gegen die Felddefinition.

Läuft beim Anlegen, beim Ändern und vor dem Verbindungstest. Jeder Fehler
nennt Feld und Ursache, damit die Oberfläche ihn am Feld anzeigen kann.
"""
from __future__ import annotations

from typing import Any

from .base import ConfigField, DriverMeta, FieldType

MASK = "•••"


def _err(field: ConfigField, text: str) -> dict[str, str]:
    return {"key": field.key, "label": field.label, "error": text}


def _is_int(value: Any) -> bool:
    try:
        return float(value) == int(float(value))
    except (TypeError, ValueError):
        return False


def validate_config(meta: DriverMeta, config: dict[str, Any], *, masked_ok: bool = False) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for field in meta.fields:
        value = config.get(field.key, field.default)
        if isinstance(value, str):
            value = value.strip()
        if masked_ok and value == MASK:
            continue
        if value in (None, ""):
            if field.required:
                errors.append(_err(field, "Pflichtfeld"))
            continue
        if field.type == FieldType.NUMBER:
            try:
                number = float(str(value).replace(",", "."))
            except ValueError:
                errors.append(_err(field, "keine Zahl"))
                continue
            if field.key in ("port", "modbus_port", "proxy_port") and not (_is_int(number) and 1 <= number <= 65535):
                errors.append(_err(field, "Port 1–65535"))
            elif field.key == "unit_id" and not (_is_int(number) and 0 <= number <= 255):
                errors.append(_err(field, "Unit-ID 0–255"))
            elif number < 0 and field.key not in ("scale", "offset"):
                errors.append(_err(field, "darf nicht negativ sein"))
        elif field.type == FieldType.BOOLEAN:
            if not isinstance(value, bool) and str(value).lower() not in ("true", "false", "0", "1"):
                errors.append(_err(field, "ja/nein erwartet"))
        elif field.type == FieldType.SELECT and field.options:
            allowed = {str(o.get("value")) for o in field.options}
            if str(value) not in allowed:
                errors.append(_err(field, "ungültige Auswahl"))
        elif field.type in (FieldType.TEXT, FieldType.PASSWORD):
            text = str(value)
            if field.key == "host" and (any(c.isspace() for c in text) or "/" in text.replace("://", "")):
                errors.append(_err(field, "nur Hostname oder IP-Adresse"))
            elif field.key.endswith("url") and not text.startswith(("http://", "https://", "mqtt://", "mqtts://", "ws://", "wss://")):
                errors.append(_err(field, "URL mit http:// oder https://"))
    return errors


def config_error(errors: list[dict[str, str]]) -> dict[str, Any]:
    first = ", ".join(f"{e['label']}: {e['error']}" for e in errors[:3])
    return {"code": "invalid_config", "message": f"Eingabe ungültig: {first}", "fields": errors}
