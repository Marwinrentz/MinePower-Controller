"""Historische Zeitreihen (aggregiert) für Charts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/history", tags=["history"])

# range → (Zeitraum, Bucket-Größe) – Buckets als timedelta, damit asyncpg
# sie nativ als PostgreSQL-interval bindet (String + CAST schlägt fehl)
RANGES: dict[str, tuple[timedelta, timedelta]] = {
    "1h": (timedelta(hours=1), timedelta(seconds=30)),
    "6h": (timedelta(hours=6), timedelta(minutes=2)),
    "24h": (timedelta(hours=24), timedelta(minutes=5)),
    "7d": (timedelta(days=7), timedelta(minutes=30)),
    "30d": (timedelta(days=30), timedelta(hours=2)),
    "365d": (timedelta(days=365), timedelta(days=1)),
}


@router.get("")
async def get_history(
    fields: str = Query("pv_power,grid_power,house_power,battery_power,battery_soc"),
    range: str = Query("24h"),
    source: str = Query("site"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from .. import startup

    span, bucket = RANGES.get(range, RANGES["24h"])
    since = datetime.now(timezone.utc) - span
    field_list = [f.strip() for f in fields.split(",") if f.strip()][:12]
    # Lange Zeiträume aus den Stundenwerten: Die Rohdaten reichen nur
    # RAW_RETENTION_DAYS zurück, das Jahr zeigte deshalb nur ein Quartal.
    hourly = startup.HOURLY_VIEW and span >= timedelta(days=30)

    result: dict[str, list[dict]] = {}
    for field in field_list:
        params = {"bucket": bucket, "field": field, "source": source, "since": since}
        if hourly:
            rows = await db.execute(
                text(
                    "SELECT time_bucket(:bucket, bucket) AS ts, avg(avg_value) AS val "
                    "FROM measurements_hourly WHERE field = :field AND source = :source AND bucket >= :since "
                    "GROUP BY ts ORDER BY ts"
                ),
                params,
            )
            result[field] = [{"time": r.ts.isoformat(), "value": round(r.val, 1)} for r in rows]
            continue
        # time_bucket ist Timescale-spezifisch; date_bin als Fallback (PG14+)
        try:
            rows = await db.execute(
                text(
                    # Alias nicht "t" nennen: Row.t ist ein SQLAlchemy-Builtin und verschattet die Spalte
                    "SELECT time_bucket(:bucket, time) AS ts, avg(value) AS val "
                    "FROM measurements WHERE field = :field AND source = :source AND time >= :since "
                    "GROUP BY ts ORDER BY ts"
                ),
                params,
            )
        except Exception:  # noqa: BLE001 – reines PostgreSQL ohne Timescale
            await db.rollback()
            rows = await db.execute(
                text(
                    "SELECT date_bin(:bucket, time, TIMESTAMPTZ '2020-01-01') AS ts, avg(value) AS val "
                    "FROM measurements WHERE field = :field AND source = :source AND time >= :since "
                    "GROUP BY ts ORDER BY ts"
                ),
                params,
            )
        result[field] = [{"time": r.ts.isoformat(), "value": round(r.val, 1)} for r in rows]
    return result


#: Größter Abstand zweier Messwerte, der als durchgehende Messung zählt (s)
MAX_SAMPLE_GAP_S = 300.0

_DAILY_RAW_SQL = """
    WITH s AS (
        SELECT time, field, value,
               extract(epoch FROM (lead(time) OVER (PARTITION BY field ORDER BY time) - time)) AS dt
        FROM measurements
        WHERE source = 'site' AND time >= :since AND field IN ('pv_power', 'house_power', 'grid_power')
    )
    SELECT date_trunc('day', time) AS day, field,
           sum(GREATEST(value, 0) * LEAST(COALESCE(dt, 0), :gap)) / 3600000.0 AS pos_kwh,
           sum(GREATEST(-value, 0) * LEAST(COALESCE(dt, 0), :gap)) / 3600000.0 AS neg_kwh
    FROM s GROUP BY day, field ORDER BY day
"""

_DAILY_HOURLY_SQL = """
    SELECT date_trunc('day', bucket) AS day, field,
           sum(avg_pos * dur) / 3600000.0 AS pos_kwh,
           sum(-avg_neg * dur) / 3600000.0 AS neg_kwh
    FROM (
        SELECT bucket, field, avg_pos, avg_neg,
               LEAST(3600.0, CASE WHEN samples > 1
                    THEN extract(epoch FROM (last_time - first_time)) * samples / (samples - 1)
                    ELSE 0 END) AS dur
        FROM measurements_hourly
        WHERE source = 'site' AND bucket >= :since AND field IN ('pv_power', 'house_power', 'grid_power')
    ) h
    GROUP BY day, field ORDER BY day
"""


@router.get("/energy")
async def energy_balance(
    range: str = Query("7d"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Tages-Energiebilanz (kWh) aus den Leistungs-Zeitreihen integriert.

    Jeder Messwert gilt bis zum nächsten, höchstens MAX_SAMPLE_GAP_S. Die
    frühere Rechnung (Summe mal mittlerer Abstand) lag bei Bezug und
    Einspeisung daneben: Der Abstand wurde nur aus den Werten einer Richtung
    bestimmt, zwei Stunden Bezug am Abend zählten damit wie ein ganzer Tag."""
    from .. import startup

    span, _bucket = RANGES.get(range, RANGES["7d"])
    since = datetime.now(timezone.utc) - span
    if startup.HOURLY_VIEW and span > timedelta(days=30):
        rows = await db.execute(text(_DAILY_HOURLY_SQL), {"since": since})
    else:
        rows = await db.execute(text(_DAILY_RAW_SQL), {"since": since, "gap": MAX_SAMPLE_GAP_S})
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        day = out.setdefault(r.day.date().isoformat(), {})
        if r.field == "grid_power":
            day["grid_import"] = round(r.pos_kwh or 0.0, 2)
            day["grid_export"] = round(r.neg_kwh or 0.0, 2)
        else:
            day[r.field] = round(r.pos_kwh or 0.0, 2)
    return out
