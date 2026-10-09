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
    span, bucket = RANGES.get(range, RANGES["24h"])
    since = datetime.now(timezone.utc) - span
    field_list = [f.strip() for f in fields.split(",") if f.strip()][:12]

    result: dict[str, list[dict]] = {}
    for field in field_list:
        # time_bucket ist Timescale-spezifisch; date_bin als Fallback (PG14+)
        try:
            rows = await db.execute(
                text(
                    # Alias nicht "t" nennen: Row.t ist ein SQLAlchemy-Builtin und verschattet die Spalte
                    "SELECT time_bucket(:bucket, time) AS ts, avg(value) AS val "
                    "FROM measurements WHERE field = :field AND source = :source AND time >= :since "
                    "GROUP BY ts ORDER BY ts"
                ),
                {"bucket": bucket, "field": field, "source": source, "since": since},
            )
        except Exception:  # noqa: BLE001 – reines PostgreSQL ohne Timescale
            await db.rollback()
            rows = await db.execute(
                text(
                    "SELECT date_bin(:bucket, time, TIMESTAMPTZ '2020-01-01') AS ts, avg(value) AS val "
                    "FROM measurements WHERE field = :field AND source = :source AND time >= :since "
                    "GROUP BY ts ORDER BY ts"
                ),
                {"bucket": bucket, "field": field, "source": source, "since": since},
            )
        result[field] = [{"time": r.ts.isoformat(), "value": round(r.val, 1)} for r in rows]
    return result


@router.get("/energy")
async def energy_balance(
    range: str = Query("7d"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Tages-Energiebilanz (kWh) aus den Leistungs-Zeitreihen integriert."""
    span, _bucket = RANGES.get(range, RANGES["7d"])
    since = datetime.now(timezone.utc) - span
    rows = await db.execute(
        text(
            """
            SELECT date_trunc('day', time) AS day, field,
                   sum(value) * (extract(epoch FROM (max(time) - min(time))) / NULLIF(count(*) - 1, 0)) / 3600000.0 AS kwh
            FROM measurements
            WHERE source = 'site' AND time >= :since
              AND field IN ('pv_power', 'house_power')
            GROUP BY day, field

            UNION ALL

            SELECT date_trunc('day', time) AS day,
                   CASE WHEN value >= 0 THEN 'grid_import' ELSE 'grid_export' END AS field,
                   sum(abs(value)) * (extract(epoch FROM (max(time) - min(time))) / NULLIF(count(*) - 1, 0)) / 3600000.0 AS kwh
            FROM measurements
            WHERE source = 'site' AND field = 'grid_power' AND time >= :since
            GROUP BY day, CASE WHEN value >= 0 THEN 'grid_import' ELSE 'grid_export' END
            ORDER BY day
            """
        ),
        {"since": since},
    )
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        day = r.day.date().isoformat()
        out.setdefault(day, {})[r.field] = round(r.kwh or 0.0, 2)
    return out
