"""Statistik & Reporting: Autarkie, Eigenverbrauch, Ersparnis, CO₂, CSV-Export."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ChargeSession, User
from ..security import get_current_user

router = APIRouter(prefix="/api/statistics", tags=["statistics"])

RANGES = {"24h": 1, "7d": 7, "30d": 30, "365d": 365}

CO2_G_PER_KWH_GRID = 380.0  # dt. Strommix (näherungsweise, konfigurierbar via settings.ui)
DEFAULT_PRICE_CT = 32.0


async def _energy_sums(db: AsyncSession, since: datetime) -> dict[str, float]:
    """Energie-Integrale (kWh) aus den site-Leistungsreihen."""
    rows = await db.execute(
        text(
            """
            WITH dt AS (
                SELECT field,
                       extract(epoch FROM (max(time) - min(time))) / NULLIF(count(*) - 1, 0) AS avg_dt
                FROM measurements
                WHERE source = 'site' AND time >= :since
                GROUP BY field
            )
            SELECT m.field,
                   sum(CASE WHEN m.value > 0 THEN m.value ELSE 0 END) * max(dt.avg_dt) / 3600000.0 AS pos_kwh,
                   sum(CASE WHEN m.value < 0 THEN -m.value ELSE 0 END) * max(dt.avg_dt) / 3600000.0 AS neg_kwh
            FROM measurements m
            JOIN dt ON dt.field = m.field
            WHERE m.source = 'site' AND m.time >= :since
              AND m.field IN ('pv_power', 'grid_power', 'house_power', 'wallbox_power',
                              'water_power', 'waste_power')
            GROUP BY m.field
            """
        ),
        {"since": since},
    )
    out: dict[str, float] = {}
    for r in rows:
        out[r.field] = float(r.pos_kwh or 0)
        if r.field == "grid_power":
            out["grid_export"] = float(r.neg_kwh or 0)
    return out


@router.get("")
async def statistics(
    range: str = Query("30d"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    days = RANGES.get(range, 30)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sums = await _energy_sums(db, since)

    pv = sums.get("pv_power", 0.0)
    grid_import = sums.get("grid_power", 0.0)
    grid_export = sums.get("grid_export", 0.0)
    consumption = pv - grid_export + grid_import  # Gesamtverbrauch inkl. Lasten/Batterie-Verluste
    self_consumed = max(0.0, pv - grid_export)

    autarky = 100.0 * (1.0 - grid_import / consumption) if consumption > 0 else 0.0
    self_consumption_rate = 100.0 * self_consumed / pv if pv > 0 else 0.0

    sessions = (await db.scalars(select(ChargeSession).where(ChargeSession.started_at >= since))).all()
    charged = sum(s.energy_kwh for s in sessions)
    charged_solar = sum(s.solar_kwh for s in sessions)

    savings_eur = self_consumed * DEFAULT_PRICE_CT / 100.0
    co2_saved_kg = self_consumed * CO2_G_PER_KWH_GRID / 1000.0

    # Verschenkter Solarstrom: Einspeisung über dem Netz-Sollwert, die keine
    # steuerbare Last aufgenommen hat. Wird vom Regelkreis je Tick gemessen
    # und hier über den Zeitraum integriert – die Kennzahl, an der sich das
    # Regelziel 'keinen Watt verschenken' messen lassen muss.
    wasted = sums.get("waste_power", 0.0)

    return {
        "range": range,
        "pv_kwh": round(pv, 1),
        "grid_import_kwh": round(grid_import, 1),
        "grid_export_kwh": round(grid_export, 1),
        "consumption_kwh": round(consumption, 1),
        "autarky_pct": round(min(100.0, max(0.0, autarky)), 1),
        "self_consumption_pct": round(min(100.0, self_consumption_rate), 1),
        "charged_kwh": round(charged, 1),
        "charged_solar_kwh": round(charged_solar, 1),
        "charged_solar_pct": round(100.0 * charged_solar / charged, 1) if charged > 0 else 0.0,
        "savings_eur": round(savings_eur, 2),
        "co2_saved_kg": round(co2_saved_kg, 1),
        "session_count": len(sessions),
        "wasted_kwh": round(wasted, 1),
        "wasted_pct": round(100.0 * wasted / pv, 1) if pv > 0 else 0.0,
        # Was der Überschuss wert gewesen wäre, hätte ihn eine Last aufgenommen
        # (Ersparnis gegenüber Netzbezug, nicht die Einspeisevergütung).
        "wasted_value_eur": round(wasted * DEFAULT_PRICE_CT / 100.0, 2),
    }


@router.get("/export.csv")
async def export_csv(
    range: str = Query("30d"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Ladevorgänge als CSV (Excel-kompatibel, Semikolon-getrennt)."""
    days = RANGES.get(range, 30)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sessions = (
        await db.scalars(
            select(ChargeSession).where(ChargeSession.started_at >= since).order_by(ChargeSession.started_at)
        )
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["ID", "Start", "Ende", "Energie (kWh)", "Solar (kWh)", "Solaranteil (%)",
                     "Kosten (EUR)", "Geraet", "Nutzer", "RFID"])
    for s in sessions:
        solar_pct = round(100.0 * s.solar_kwh / s.energy_kwh, 1) if s.energy_kwh else 0
        writer.writerow([
            s.id,
            s.started_at.isoformat(),
            s.ended_at.isoformat() if s.ended_at else "",
            f"{s.energy_kwh:.2f}".replace(".", ","),
            f"{s.solar_kwh:.2f}".replace(".", ","),
            str(solar_pct).replace(".", ","),
            f"{s.cost_eur:.2f}".replace(".", ","),
            s.device_id,
            s.user_id or "",
            s.rfid_tag or "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=minepower_sessions_{range}.csv"},
    )


@router.get("/tariff")
async def tariff_prices(_: User = Depends(get_current_user)):
    """Kommende Stundenpreise (falls dynamischer Tarif konfiguriert)."""
    from ..core import runtime

    loop = runtime.get_loop()
    if loop.tariff is None:
        return {"provider": "none", "prices": []}
    return {"provider": loop.tariff.provider, "current_ct": loop.tariff.current_price_ct(),
            "cheap_limit_ct": loop.tariff.cheap_limit_ct, "prices": loop.tariff.upcoming()}


@router.get("/forecast")
async def forecast(_: User = Depends(get_current_user)):
    from ..core import runtime

    loop = runtime.get_loop()
    svc = getattr(loop, "forecast", None)
    return svc.summary() if svc else {"provider": "none"}
