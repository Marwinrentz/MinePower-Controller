"""Höchstzahl aktiver Geräte je Klasse.

* **Netzzähler: 1.** Es gibt einen Netzanschlusspunkt; ein zweiter Zähler
  würde den ersten überschreiben.
* **Batterie (eigenes, steuerbares Gerät): 1.** Entladesperre und
  Hausversorgung werden je Gerät gerechnet; zwei Speicher würden die
  Hauslast doppelt decken. Batterien hinter Hybrid-Wechselrichtern werden
  davon nicht erfasst – deren Werte werden summiert.
* Wechselrichter, Wallboxen/Fahrzeuge und Warmwasser: beliebig viele.
  Verteilung über die Prioritätskette.
"""
from __future__ import annotations

from sqlalchemy import func, select

CATEGORY_LIMITS: dict[str, int] = {"meter": 1, "battery": 1}

LIMIT_LABEL = {"meter": "Netzzähler", "battery": "Batterie"}


def limit_error(category: str) -> dict:
    limit = CATEGORY_LIMITS[category]
    return {
        "code": "category_limit",
        "category": category,
        "limit": limit,
        "message": f"{LIMIT_LABEL.get(category, category)}: höchstens {limit} aktives Gerät",
    }


async def active_count(db, category: str, exclude_id: int | None = None) -> int:
    from ..models import Device

    query = select(func.count(Device.id)).where(Device.category == category, Device.enabled.is_(True))
    if exclude_id is not None:
        query = query.where(Device.id != exclude_id)
    return int(await db.scalar(query) or 0)


async def enforce_on_startup() -> list[str]:
    """Altbestand mit mehreren aktiven Zählern bzw. Batterien: das älteste
    Gerät bleibt aktiv, weitere werden deaktiviert (nicht gelöscht)."""
    from ..db import async_session
    from ..models import Device
    from ..services.audit import log_event

    disabled: list[str] = []
    async with async_session() as db:
        for category, limit in CATEGORY_LIMITS.items():
            rows = (await db.scalars(
                select(Device).where(Device.category == category, Device.enabled.is_(True)).order_by(Device.id)
            )).all()
            for row in rows[limit:]:
                row.enabled = False
                disabled.append(row.name)
        await db.commit()
    for name in disabled:
        await log_event(f"Gerät deaktiviert (Limit je Klasse): {name}", category="config")
    return disabled
