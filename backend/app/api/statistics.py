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

#: Vorgaben für die Bewertung, einstellbar im Abschnitt 'statistics' der
#: Einstellungen: Strompreis und Einspeisevergütung in ct/kWh, CO₂ des
#: Netzstroms in g/kWh (deutscher Strommix, näherungsweise).
CO2_G_PER_KWH_GRID = 380.0
DEFAULT_PRICE_CT = 32.0
DEFAULT_FEED_IN_CT = 0.0


async def _valuation(db: AsyncSession) -> tuple[float, float, float]:
    """(Strompreis ct/kWh, Einspeisevergütung ct/kWh, CO₂ g/kWh)."""
    from ..models import Setting

    row = await db.get(Setting, "statistics")
    value = row.value if row is not None and isinstance(row.value, dict) else {}

    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(value.get(key, default))
        except (TypeError, ValueError):
            return default
        return v if lo <= v <= hi else default

    return (num("price_ct", DEFAULT_PRICE_CT, 0.0, 200.0), num("feed_in_ct", DEFAULT_FEED_IN_CT, 0.0, 100.0),
            num("co2_g_per_kwh", CO2_G_PER_KWH_GRID, 0.0, 2000.0))


#: Größter Abstand zweier Messwerte, der noch als durchgehende Messung
#: zählt (s). Dahinter liegt eine Lücke (Anwendung aus, Datenbank weg), über
#: die nicht hinwegintegriert wird.
MAX_SAMPLE_GAP_S = 300.0
#: Ab diesem Zeitraum (Tage) kommen die Energien aus den Stundenwerten:
#: Rohdaten reichen nur ``RAW_RETENTION_DAYS`` zurück.
HOURLY_FROM_DAYS = 31

_ENERGY_FIELDS = "('pv_power', 'grid_power', 'house_power', 'wallbox_power', 'water_power', 'waste_power')"

#: Energie aus Rohdaten: jeder Messwert gilt bis zum nächsten, höchstens aber
#: MAX_SAMPLE_GAP_S. Früher wurde die Summe aller Werte mit dem mittleren
#: Abstand über den ganzen Zeitraum multipliziert – nach einem Ausfall zählte
#: die Lücke damit als Zeit mit Durchschnittsleistung.
_RAW_ENERGY_SQL = f"""
    WITH s AS (
        SELECT field, value,
               extract(epoch FROM (lead(time) OVER (PARTITION BY field ORDER BY time) - time)) AS dt
        FROM measurements
        WHERE source = 'site' AND time >= :since AND field IN {_ENERGY_FIELDS}
    )
    SELECT field,
           sum(GREATEST(value, 0) * LEAST(COALESCE(dt, 0), :gap)) / 3600000.0 AS pos_kwh,
           sum(GREATEST(-value, 0) * LEAST(COALESCE(dt, 0), :gap)) / 3600000.0 AS neg_kwh
    FROM s GROUP BY field
"""

#: Energie aus Stundenwerten: Mittel der Stunde mal der Zeit, die in dieser
#: Stunde tatsächlich gemessen wurde.
_HOURLY_ENERGY_SQL = f"""
    SELECT field,
           sum(avg_pos * dur) / 3600000.0 AS pos_kwh,
           sum(-avg_neg * dur) / 3600000.0 AS neg_kwh
    FROM (
        SELECT bucket, field, avg_pos, avg_neg,
               LEAST(3600.0, CASE
                    WHEN samples > 1 THEN extract(epoch FROM (last_time - first_time)) * samples / (samples - 1)
                    -- ein einzelner Wert genau zur vollen Stunde ist ein aus der
                    -- Sicherung eingespielter Stundenwert und gilt für die ganze Stunde
                    WHEN first_time = bucket THEN 3600.0
                    ELSE 0 END) AS dur
        FROM measurements_hourly
        WHERE source = 'site' AND bucket >= :since AND field IN {_ENERGY_FIELDS}
    ) h
    GROUP BY field
"""


async def _energy_sums(db: AsyncSession, since: datetime, days: int = 30) -> dict[str, float]:
    """Energie-Integrale (kWh) aus den site-Leistungsreihen."""
    from .. import startup

    if days >= HOURLY_FROM_DAYS and startup.HOURLY_VIEW:
        rows = await db.execute(text(_HOURLY_ENERGY_SQL), {"since": since})
    else:
        rows = await db.execute(text(_RAW_ENERGY_SQL), {"since": since, "gap": MAX_SAMPLE_GAP_S})
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
    sums = await _energy_sums(db, since, days)

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

    # Ersparnis: Jede selbst verbrauchte kWh spart den Bezugspreis, kostet
    # aber die Vergütung, die es fürs Einspeisen gegeben hätte.
    price_ct, feed_in_ct, co2_g = await _valuation(db)
    savings_eur = self_consumed * max(0.0, price_ct - feed_in_ct) / 100.0
    co2_saved_kg = self_consumed * co2_g / 1000.0

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
        "wasted_value_eur": round(wasted * max(0.0, price_ct - feed_in_ct) / 100.0, 2),
        # Womit gerechnet wurde (Einstellungen → Benachrichtigungen & Auswertung)
        "price_ct": price_ct,
        "feed_in_ct": feed_in_ct,
        "co2_g_per_kwh": co2_g,
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
            "cheap_limit_ct": loop.tariff.cheap_limit_ct, "prices": loop.tariff.upcoming(),
            **_auto_fields(loop.tariff)}


@router.get("/forecast")
async def forecast(_: User = Depends(get_current_user)):
    from ..core import runtime

    loop = runtime.get_loop()
    svc = getattr(loop, "forecast", None)
    return svc.summary() if svc else {"provider": "none"}


def _auto_fields(tariff) -> dict:
    """Wirksame Grenze und Stand der automatischen Grenze fuer die Oberflaeche."""
    effective = getattr(tariff, "effective_limit_ct", None)
    auto = tariff.auto_status() if hasattr(tariff, "auto_status") else None
    return {
        "effective_limit_ct": effective() if callable(effective) else tariff.cheap_limit_ct,
        "auto_limit": bool(getattr(tariff, "auto_limit", False)),
        "auto_active": auto is not None,
        "auto_lowest_ct": round(auto.lowest_ct, 2) if auto else None,
        "auto_window_h": round(auto.window_h, 2) if auto else None,
    }
