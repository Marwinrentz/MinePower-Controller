"""Gemeinsame Bausteine der generischen Treiber (HTTP, MQTT, Home Assistant,
Tasmota, Modbus mit Registerprofil).

* ``extract``: Wert aus JSON per Punktpfad (``emeters.0.power``,
  ``StatusSNS.SML.Power``). Leerer Pfad = die Antwort selbst ist die Zahl.
* ``number``: Zahl aus beliebiger Darstellung ('1.234,5", '12 W", "on").
* ``watts``: Leistungswert mit Einheit (W, kW) und Faktor in Watt.
* Feldvorlagen, damit alle generischen Treiber dieselben Bezeichnungen haben.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import ConfigField, FieldType
from .validation import DriverError


def extract(data: Any, path: str | None) -> Any:
    """Punktpfad in JSON auflösen; Listen per Index (``a.0.b``)."""
    if not path:
        return data
    node = data
    for part in str(path).strip().split("."):
        if part == "":
            continue
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                raise DriverError(f"Pfad '{path}': Index '{part}' nicht vorhanden")
        elif isinstance(node, dict):
            if part not in node:
                raise DriverError(f"Pfad '{path}': Feld '{part}' nicht vorhanden")
            node = node[part]
        else:
            raise DriverError(f"Pfad '{path}': '{part}' nicht auflösbar")
    return node


_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def number(value: Any, *, what: str = "Wert") -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        low = text.lower()
        if low in ("on", "true", "ein"):
            return 1.0
        if low in ("off", "false", "aus"):
            return 0.0
        # Dezimaltrennzeichen: '1.234,5" (de), '1,234.5" (en), '12,5"
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".")
        match = _NUM.search(text)
        if match:
            return float(match.group(0))
    raise DriverError(f"{what}: keine Zahl ({value!r})")


def watts(value: Any, unit: str | None = None, factor: float = 1.0, *, what: str = "Leistung") -> float:
    """Leistung in Watt; 'kW" wird umgerechnet."""
    n = number(value, what=what)
    if unit and unit.strip().lower() == "kw":
        n *= 1000.0
    return n * float(factor or 1.0)


def parse_payload(raw: bytes | str) -> Any:
    """MQTT-/HTTP-Rohdaten: JSON, sonst Klartext."""
    text = raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text.strip()


def flag(value: Any) -> bool:
    return bool(value) and str(value).lower() not in ("false", "0", "no", "nein")


# ---------------------------------------------------------------- Feldvorlagen

def path_field(key: str, label: str, *, required: bool = True, placeholder: str = "") -> ConfigField:
    return ConfigField(key=key, label=label, required=required, placeholder=placeholder or None,
                       help="JSON-Pfad, z. B. data.power; leer = ganze Antwort")


SCALE_FIELD = ConfigField(key="scale", label="Faktor", type=FieldType.NUMBER, default=1.0, required=False,
                          help="Rohwert × Faktor = Watt (z. B. 1000 bei kW)")
INVERT_FIELD = ConfigField(key="invert", label="Vorzeichen umkehren", type=FieldType.BOOLEAN, default=False,
                           required=False, help="+ muss Netzbezug sein")
RATED_FIELD = ConfigField(key="rated_power", label="Nennleistung (W)", type=FieldType.NUMBER, default=3000)
