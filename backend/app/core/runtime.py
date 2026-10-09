"""Zugriff auf die laufende Control-Loop-Instanz (von main.py gesetzt)."""
from __future__ import annotations

from .loop import ControlLoop

loop: ControlLoop | None = None


def get_loop() -> ControlLoop:
    if loop is None:
        raise RuntimeError("Control-Loop wurde noch nicht initialisiert")
    return loop
