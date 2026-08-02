"""Dynamische Stromtarife: Tibber & aWATTar (EPEX-Spot).

Preise werden stündlich aktualisiert und dem Control-Loop als
`current_price_ct()` / `is_cheap_hour()` bereitgestellt (Modus 'price').
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from sqlalchemy import select

from ..db import async_session
from ..models import Setting

log = logging.getLogger(__name__)

TIBBER_URL = "https://api.tibber.com/v1-beta/gql"
AWATTAR_URLS = {"de": "https://api.awattar.de/v1/marketdata", "at": "https://api.awattar.at/v1/marketdata"}

TIBBER_QUERY = """
{ viewer { homes { currentSubscription { priceInfo {
  today { total startsAt } tomorrow { total startsAt }
}}}}}
"""


class TariffService:
    def __init__(self) -> None:
        self.provider = "none"
        self.cheap_limit_ct = 15.0
        self.config: dict = {}
        self.prices: list[tuple[datetime, float]] = []  # (Stundenbeginn UTC, ct/kWh)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="tariff-refresh")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            try:
                await self._load_config()
                if self.provider != "none":
                    await self._refresh()
            except Exception as exc:  # noqa: BLE001
                log.warning("Tarif-Update fehlgeschlagen: %s", exc)
            await asyncio.sleep(1800)  # alle 30 min reicht für Stundenpreise

    async def _load_config(self) -> None:
        async with async_session() as session:
            row = await session.scalar(select(Setting).where(Setting.key == "tariff"))
        cfg = (row.value if row else {}) or {}
        self.provider = cfg.get("provider", "none")
        self.cheap_limit_ct = float(cfg.get("cheap_limit_ct", 15))
        self.config = cfg

    async def _refresh(self) -> None:
        if self.provider == "tibber":
            await self._fetch_tibber()
        elif self.provider == "awattar":
            await self._fetch_awattar()

    async def _fetch_tibber(self) -> None:
        token = self.config.get("tibber_token", "")
        if not token:
            return
        async with aiohttp.ClientSession() as http:
            async with http.post(
                TIBBER_URL,
                json={"query": TIBBER_QUERY},
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        homes = data["data"]["viewer"]["homes"]
        if not homes:
            return
        info = homes[0]["currentSubscription"]["priceInfo"]
        prices = []
        for entry in (info.get("today") or []) + (info.get("tomorrow") or []):
            start = datetime.fromisoformat(entry["startsAt"]).astimezone(timezone.utc)
            prices.append((start, float(entry["total"]) * 100.0))  # € → ct
        self.prices = sorted(prices)
        log.info("Tibber: %d Stundenpreise geladen", len(self.prices))

    async def _fetch_awattar(self) -> None:
        url = AWATTAR_URLS.get(self.config.get("awattar_region", "de"), AWATTAR_URLS["de"])
        async with aiohttp.ClientSession() as http:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                resp.raise_for_status()
                data = await resp.json()
        surcharge = float(self.config.get("surcharge_ct", 0))  # Netzentgelte etc. optional
        self.prices = sorted(
            (
                datetime.fromtimestamp(e["start_timestamp"] / 1000, tz=timezone.utc),
                e["marketprice"] / 10.0 + surcharge,  # €/MWh → ct/kWh
            )
            for e in data.get("data", [])
        )
        log.info("aWATTar: %d Stundenpreise geladen", len(self.prices))

    # ------------------------------------------------------------ Abfragen

    def current_price_ct(self) -> float | None:
        now = datetime.now(timezone.utc)
        current = None
        for start, price in self.prices:
            if start <= now:
                current = price
            else:
                break
        return current

    def is_cheap_hour(self) -> bool:
        price = self.current_price_ct()
        return price is not None and price <= self.cheap_limit_ct

    def upcoming(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        return [
            {"start": start.isoformat(), "price_ct": round(price, 2)}
            for start, price in self.prices
            if start >= now.replace(minute=0, second=0, microsecond=0)
        ]
