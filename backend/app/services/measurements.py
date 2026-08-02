"""Gepufferter Zeitreihen-Writer für die TimescaleDB-Hypertable."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import insert

from ..db import async_session
from ..models import Measurement

log = logging.getLogger(__name__)


class MeasurementBuffer:
    """Sammelt Messwerte im Speicher und schreibt sie als Batch in die DB,
    damit der Control-Loop nicht pro Tick auf die DB warten muss."""

    def __init__(self, flush_interval_s: float = 15.0) -> None:
        self.flush_interval_s = flush_interval_s
        self._rows: list[dict] = []
        self._last_flush = datetime.now(timezone.utc)

    def add(self, source: str, field: str, value: float, time: datetime | None = None) -> None:
        if value is None:
            return
        self._rows.append(
            {
                "time": time or datetime.now(timezone.utc),
                "source": source,
                "field": field,
                "value": float(value),
            }
        )

    def due(self) -> bool:
        return (datetime.now(timezone.utc) - self._last_flush).total_seconds() >= self.flush_interval_s

    async def flush(self) -> None:
        if not self._rows:
            self._last_flush = datetime.now(timezone.utc)
            return
        rows, self._rows = self._rows, []
        self._last_flush = datetime.now(timezone.utc)
        try:
            async with async_session() as session:
                await session.execute(insert(Measurement), rows)
                await session.commit()
        except Exception as exc:  # noqa: BLE001 – DB-Ausfall darf den Loop nicht stoppen
            log.error("Messwert-Flush fehlgeschlagen (%d Zeilen verworfen): %s", len(rows), exc)
