"""Zeitfenster-Auswertung für den Modus 'schedule'."""
from __future__ import annotations

from datetime import datetime, time
from .clock import local_now


def _parse(t: str) -> time:
    hh, mm = t.split(":")
    return time(int(hh), int(mm))


def window_active(days_mask: int, start: str, end: str, now: datetime) -> bool:
    """True, wenn `now` im Fenster liegt. Fenster über Mitternacht (22:00–06:00)
    werden unterstützt; days_mask bezieht sich auf den Starttag (Mo=1 … So=64)."""
    start_t, end_t = _parse(start), _parse(end)
    weekday_bit = 1 << now.weekday()
    prev_bit = 1 << ((now.weekday() - 1) % 7)
    now_t = now.time()

    if start_t <= end_t:
        return bool(days_mask & weekday_bit) and start_t <= now_t < end_t
    # über Mitternacht: heutiger Abschnitt ab start ODER gestriger bis end
    if now_t >= start_t:
        return bool(days_mask & weekday_bit)
    if now_t < end_t:
        return bool(days_mask & prev_bit)
    return False


def any_window_active(schedules: list[dict], now: datetime | None = None) -> bool:
    now = now or local_now()
    return any(
        window_active(s["days_mask"], s["start_time"], s["end_time"], now)
        for s in schedules
        if s.get("enabled", True)
    )
