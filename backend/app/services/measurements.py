"""Zeitreihen-Writer mit Puffer und Nachschreiben nach einem Datenbank-Ausfall.

Messwerte sammeln sich im Speicher und gehen alle paar Sekunden als Paket in
die Datenbank. Ist sie nicht erreichbar (Neustart, Update, Stromausfall am
Datenbank-Host), ging bisher jedes Paket verloren – der Verlauf hatte danach
eine Lücke. Jetzt:

* Ein fehlgeschlagenes Paket wird an eine **Spool-Datei** im Datenvolume
  angehängt (eine Zeile je Messwert, mit ``fsync``). Sie überlebt auch einen
  Neustart der Anwendung, während die Datenbank noch fehlt.
* Sobald wieder ein Paket ankommt, wird die Spool-Datei **nachgeschrieben**
  und gelöscht.
* Der Primärschlüssel ``(time, source, field)`` und ``ON CONFLICT DO NOTHING``
  machen das Nachschreiben wiederholbar: Bricht es mittendrin ab, entstehen
  beim nächsten Versuch **keine Duplikate**.
* Das Schreiben läuft neben dem Regelkreis (`flush_soon`), nie in ihm: Eine
  hängende Datenbank hält die Regelung nicht an.

Grenzen: Die Spool-Datei wächst höchstens bis ``SPOOL_MAX_BYTES``; danach
werden neue Werte verworfen und gezählt. Was beim harten Abbruch der
Anwendung (``kill -9``, Stromausfall) noch im Speicher lag – höchstens ein
Schreibintervall –, ist weg.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db import async_session, engine
from ..models import Measurement

log = logging.getLogger(__name__)

#: Zeilen je INSERT (asyncpg erlaubt 32767 Parameter, vier je Zeile)
CHUNK_ROWS = 5000
#: Obergrenze der Spool-Datei: rund 2,5 Mio. Messwerte, etwa zehn Tage
SPOOL_MAX_BYTES = 256 * 1024 * 1024
#: Obergrenze im Speicher, falls auch die Spool-Datei nicht schreibbar ist
BACKLOG_MAX_ROWS = 100_000
#: Zeitlimit für einen Schreibvorgang im Hintergrund (s)
FLUSH_TIMEOUT_S = 120.0


def default_spool_path() -> Path | None:
    """Spool-Datei im Datenverzeichnis; None, wenn es nicht beschreibbar ist."""
    from ..config import get_settings

    try:
        directory = Path(get_settings().data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "measurements.spool.jsonl"
    except OSError:
        return None


class MeasurementBuffer:
    """Sammelt Messwerte und schreibt sie als Paket in die DB (siehe Modul)."""

    def __init__(self, flush_interval_s: float = 15.0, spool_path: Path | None | str = "auto") -> None:
        self.flush_interval_s = flush_interval_s
        self._rows: list[dict] = []
        #: Fehlgeschlagene Zeilen, die nicht in die Spool-Datei passten
        self._backlog: list[dict] = []
        self._last_flush = datetime.now(timezone.utc)
        self._spool_path: Path | None = default_spool_path() if spool_path == "auto" else spool_path  # type: ignore[assignment]
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        # --- Zustand für Diagnose und Protokoll ---
        self.failures = 0            # fehlgeschlagene Schreibversuche in Folge
        self.last_error: str | None = None
        self.dropped = 0             # verworfene Zeilen (Spool und Speicher voll)
        self.replayed = 0            # nachgeschriebene Zeilen seit Start

    # ------------------------------------------------------------ Sammeln

    def add(self, source: str, field: str, value: float, time: datetime | None = None) -> None:
        if value is None:
            return
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        if number != number or number in (float("inf"), float("-inf")):
            return  # NaN/Unendlich sind keine Messwerte
        self._rows.append(
            {"time": time or datetime.now(timezone.utc), "source": source, "field": field, "value": number}
        )

    def due(self) -> bool:
        return (datetime.now(timezone.utc) - self._last_flush).total_seconds() >= self.flush_interval_s

    # ------------------------------------------------------------ Zustand

    @property
    def spooled_bytes(self) -> int:
        try:
            return self._spool_path.stat().st_size if self._spool_path and self._spool_path.exists() else 0
        except OSError:
            return 0

    def status(self) -> dict:
        return {
            "pending_rows": len(self._rows),
            "backlog_rows": len(self._backlog),
            "spool_bytes": self.spooled_bytes,
            "failures": self.failures,
            "last_error": self.last_error,
            "dropped": self.dropped,
            "replayed": self.replayed,
        }

    # ------------------------------------------------------------ Schreiben

    def flush_soon(self) -> None:
        """Schreiben im Hintergrund anstoßen – der Aufrufer wartet nicht."""
        self._last_flush = datetime.now(timezone.utc)
        if self._task is not None and not self._task.done():
            return  # der laufende Versuch nimmt die neuen Zeilen beim nächsten Mal mit
        self._task = asyncio.create_task(self._flush_guarded(), name="measurement-flush")

    async def _flush_guarded(self) -> None:
        try:
            await asyncio.wait_for(self.flush(), timeout=FLUSH_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.error("Messwerte: Schreiben nach %.0f s abgebrochen (Datenbank antwortet nicht)", FLUSH_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 – Hintergrundaufgabe darf nie laut scheitern
            log.error("Messwerte: unerwarteter Fehler beim Schreiben: %s", exc)

    async def flush(self) -> bool:
        """Gesammelte Zeilen schreiben; danach Liegengebliebenes nachschreiben.
        Liefert True, wenn nichts mehr aussteht."""
        async with self._lock:
            self._last_flush = datetime.now(timezone.utc)
            rows, self._rows = self._rows, []
            if rows:
                try:
                    await _write(rows)
                except asyncio.CancelledError:
                    self._keep(rows)
                    raise
                except Exception as exc:  # noqa: BLE001 – DB-Ausfall darf den Loop nicht stoppen
                    self._keep(rows)
                    self.failures += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                    # Nicht jede Runde melden: einmal beim Ausfall, dann seltener.
                    if self.failures == 1 or self.failures % 40 == 0:
                        log.error("Messwerte nicht geschrieben (%d Zeilen gesichert, Versuch %d): %s",
                                  len(rows), self.failures, self.last_error)
                    return False
            elif not self._backlog and not self.spooled_bytes:
                return True
            return await self._replay()

    def _keep(self, rows: list[dict]) -> None:
        """Fehlgeschlagene Zeilen sichern: Spool-Datei, sonst Speicher."""
        if self._spool_append(rows):
            return
        room = BACKLOG_MAX_ROWS - len(self._backlog)
        if room < len(rows):
            self.dropped += len(rows) - max(room, 0)
            rows = rows[:max(room, 0)]
        self._backlog.extend(rows)

    async def _replay(self) -> bool:
        """Spool-Datei und Speicher-Rückstand nachschreiben."""
        had_failures = self.failures > 0
        try:
            count = 0
            for chunk in self._spool_chunks():
                await _write(chunk)
                count += len(chunk)
            if self._backlog:
                await _write(self._backlog)
                count += len(self._backlog)
                self._backlog = []
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            return False
        self._spool_remove()
        self.replayed += count
        if count or had_failures:
            log.info("Messwerte: Datenbank wieder erreichbar, %d Zeilen nachgeschrieben", count)
        self.failures = 0
        self.last_error = None
        return True

    async def close(self) -> None:
        """Beim Beenden: laufenden Versuch abwarten, dann ein letztes Mal
        schreiben. Was nicht mehr in die Datenbank geht, liegt im Spool."""
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            await asyncio.wait_for(self.flush(), timeout=10)
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            rows, self._rows = self._rows, []
            self._keep(rows)
            log.warning("Messwerte beim Beenden nicht geschrieben, im Spool gesichert: %s", exc)

    # ------------------------------------------------------------ Spool-Datei

    def _spool_append(self, rows: list[dict]) -> bool:
        path = self._spool_path
        if path is None or not rows:
            return path is not None
        try:
            if path.exists() and path.stat().st_size >= SPOOL_MAX_BYTES:
                self.dropped += len(rows)
                if self.dropped == len(rows):
                    log.error("Messwert-Spool voll (%d MB) – weitere Werte werden verworfen",
                              SPOOL_MAX_BYTES // (1024 * 1024))
                return True  # bewusst verworfen, nicht zusätzlich im Speicher halten
            with open(path, "a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps({"t": r["time"].isoformat(), "s": r["source"], "f": r["field"],
                                        "v": r["value"]}, separators=(",", ":")))
                    f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            return True
        except OSError as exc:
            log.warning("Messwert-Spool nicht schreibbar (%s) – Werte bleiben im Speicher", exc)
            return False

    def _spool_chunks(self) -> Iterator[list[dict]]:
        path = self._spool_path
        if path is None or not path.exists():
            return
        chunk: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                row = _parse_spool_line(line)
                if row is None:
                    continue  # abgeschnittene letzte Zeile nach einem Absturz
                chunk.append(row)
                if len(chunk) >= CHUNK_ROWS:
                    yield chunk
                    chunk = []
        if chunk:
            yield chunk

    def _spool_remove(self) -> None:
        path = self._spool_path
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Messwert-Spool nicht löschbar: %s", exc)


def _parse_spool_line(line: str) -> dict | None:
    try:
        raw = json.loads(line)
        return {"time": datetime.fromisoformat(raw["t"]), "source": str(raw["s"]), "field": str(raw["f"]),
                "value": float(raw["v"])}
    except (ValueError, KeyError, TypeError):
        return None


async def _write(rows: list[dict]) -> None:
    """Zeilen einfügen; vorhandene (gleiche Zeit, Quelle, Größe) überspringen."""
    insert = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    async with async_session() as session:
        for start in range(0, len(rows), CHUNK_ROWS):
            stmt = insert(Measurement).on_conflict_do_nothing(index_elements=["time", "source", "field"])
            await session.execute(stmt, rows[start:start + CHUNK_ROWS])
        await session.commit()
