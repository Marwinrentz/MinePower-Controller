"""Ortszeit der Anlage.

Der Container läuft in UTC. Zeitfenster ('06:00–08:00"), Abfahrtszeiten und
die Solarprognose meinen aber die Uhr am Haus. Ein naives `datetime.now()`
lieferte UTC – ein Zeitplan 20–22 Uhr heizte deshalb von 22 bis 24 Uhr
Ortszeit, also 'außerhalb des Zeitplans".

Zeitzone: Umgebungsvariable `TZ`, sonst Europe/Berlin.
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Europe/Berlin"


def local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("TZ") or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def local_now() -> datetime:
    """Aktuelle Ortszeit als naiver Zeitstempel (wie früher `datetime.now()`)."""
    return datetime.now(local_tz()).replace(tzinfo=None)
