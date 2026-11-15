"""Diagnosedaten anonymisieren, bevor sie das Haus verlassen.

Ersetzt in beliebig verschachtelten Daten und in Text:

* IPv4-Adressen       → ``host-1``, ``host-2`` … (Ports bleiben)
* E-Mail-Adressen     → ``nutzer-1@example.invalid`` …
* Fahrgestellnummern  → ``VIN-1`` …
* MAC-Adressen        → ``mac-1`` …
* Gerätenamen         → ``Gerät 1`` … (die Namen vergibt der Betreiber, oft
  mit Personen- oder Ortsbezug)
* Zugangsdaten        → ``***`` (über core/logsafe.py)

Gleiche Werte bekommen denselben Platzhalter. Die Zuordnung lebt nur für
einen Export und wird nirgends gespeichert.
"""
from __future__ import annotations

import re
from typing import Any

from ..core.logsafe import redact

_IPV4 = re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
#: Schlüssel, deren Wert nie in den Export gehört
_SECRET_KEYS = re.compile(r"(pass|token|secret|api_?key|auth|credential|jwt)", re.I)
#: Schlüssel mit einem frei vergebenen Namen
_NAME_KEYS = {"name", "device", "device_name"}


class Anonymizer:
    def __init__(self, device_names: list[str] | None = None) -> None:
        self._maps: dict[str, dict[str, str]] = {"host": {}, "mail": {}, "vin": {}, "mac": {}}
        # Längste zuerst: 'Wallbox Garage' vor 'Wallbox'
        names = sorted({n for n in (device_names or []) if n and len(n) >= 3}, key=len, reverse=True)
        self._names = {name: f"Gerät {i}" for i, name in enumerate(sorted(names), start=1)}
        self._name_order = names

    def _placeholder(self, kind: str, value: str, template: str) -> str:
        table = self._maps[kind]
        if value not in table:
            table[value] = template.format(len(table) + 1)
        return table[value]

    def text(self, value: str) -> str:
        out = redact(value)
        for name in self._name_order:
            out = out.replace(name, self._names[name])
        out = _EMAIL.sub(lambda m: self._placeholder("mail", m.group(0).lower(), "nutzer-{}@example.invalid"), out)
        out = _MAC.sub(lambda m: self._placeholder("mac", m.group(0).lower(), "mac-{}"), out)
        out = _IPV4.sub(lambda m: self._placeholder("host", m.group(0), "host-{}"), out)
        out = _VIN.sub(lambda m: self._placeholder("vin", m.group(0), "VIN-{}") if _looks_like_vin(m.group(0))
                       else m.group(0), out)
        return out

    def scrub(self, value: Any, key: str = "") -> Any:
        """Verschachtelte Daten durchgehen (dict, list, str)."""
        if isinstance(value, dict):
            return {k: self.scrub(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(v, key) for v in value]
        if isinstance(value, str):
            if key and _SECRET_KEYS.search(key) and value not in ("", "•••"):
                return "***"
            if key in _NAME_KEYS and value in self._names:
                return self._names[value]
            return self.text(value)
        return value


def _looks_like_vin(candidate: str) -> bool:
    """17 Zeichen aus Ziffern und Großbuchstaben gibt es auch sonst (Hashes,
    Kennungen). Eine Fahrgestellnummer mischt beides."""
    return any(c.isdigit() for c in candidate) and any(c.isalpha() for c in candidate)
