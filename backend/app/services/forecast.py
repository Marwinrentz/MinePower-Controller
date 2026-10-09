"""Solarprognose über forecast.solar (kostenlose Public API).

Die Prognose ist hier **kein Deko-Widget**, sondern geht aktiv in
Regelentscheidungen ein:

* :meth:`is_transient_dip` – Die Erzeugung ist gerade eingebrochen, die
  Prognose für die nächste halbe Stunde ist aber weiterhin hoch. Dann zieht
  nur eine Wolke durch, und der Regelkern hält eine laufende Last, statt sie
  abzuschalten und kurz darauf mit voller Start-Hysterese neu zu starten.
* :meth:`expected_surplus_kwh` – Wie viel nutzbarer Überschuss ist bis morgen
  früh noch zu erwarten? Damit kann die Zielladung vorausschauend früher
  takten, wenn absehbar ist, dass die Sonne bis zur Deadline nicht reicht.

Beides ist bewusst konservativ ausgelegt: Ohne konfigurierte Prognose oder
bei veralteten Daten liefern die Methoden 'keine Aussage', und die Regelung
verhält sich exakt wie vorher.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone

import aiohttp

from ..core.clock import local_now
from sqlalchemy import select

from ..db import async_session
from ..models import Setting

log = logging.getLogger(__name__)

#: Ab welcher relativen Unterschreitung der Prognose von einem Einbruch
#: gesprochen wird (0,6 = aktuelle Erzeugung unter 60 % der Prognose).
DIP_RATIO = 0.6
#: Wie weit vorausgeschaut wird, um die Erholung zu beurteilen.
RECOVERY_HORIZON_MIN = 30
#: Prognosedaten älter als das gelten als unbrauchbar (Netzwerkausfall o. Ä.).
MAX_AGE_S = 6 * 3600


class ForecastService:
    def __init__(self) -> None:
        self.provider = "none"
        self.config: dict = {}
        self.watts: dict[str, float] = {}       # ISO-Zeit → W
        self.daily_kwh: dict[str, float] = {}   # Datum → kWh
        self.updated_at: datetime | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="forecast-refresh")

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
                if self.provider == "forecast_solar":
                    await self._fetch()
            except Exception as exc:  # noqa: BLE001
                log.warning("Prognose-Update fehlgeschlagen: %s", exc)
            await asyncio.sleep(3600)  # forecast.solar Rate-Limit beachten

    async def _load_config(self) -> None:
        async with async_session() as session:
            row = await session.scalar(select(Setting).where(Setting.key == "forecast"))
        self.config = (row.value if row else {}) or {}
        self.provider = self.config.get("provider", "none")

    async def _fetch(self) -> None:
        lat = self.config.get("lat")
        lon = self.config.get("lon")
        kwp = self.config.get("kwp")
        if lat is None or lon is None or not kwp:
            return
        dec = self.config.get("declination", 30)
        az = self.config.get("azimuth", 0)  # 0 = Süd
        url = f"https://api.forecast.solar/estimate/{lat}/{lon}/{dec}/{az}/{kwp}"
        async with aiohttp.ClientSession() as http:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                resp.raise_for_status()
                data = await resp.json()
        result = data.get("result", {})
        self.watts = {k: float(v) for k, v in (result.get("watts") or {}).items()}
        self.daily_kwh = {k: v / 1000.0 for k, v in (result.get("watt_hours_day") or {}).items()}
        self.updated_at = datetime.now(timezone.utc)
        log.info("Solarprognose aktualisiert: %s", {k: round(v, 1) for k, v in self.daily_kwh.items()})

    # ------------------------------------------------------------ Auswertung

    @property
    def available(self) -> bool:
        """Nur frische Prognosedaten dürfen Entscheidungen beeinflussen."""
        if self.provider != "forecast_solar" or not self.watts or self.updated_at is None:
            return False
        return (datetime.now(timezone.utc) - self.updated_at).total_seconds() < MAX_AGE_S

    def _points(self) -> list[tuple[datetime, float]]:
        """Prognosepunkte als (lokale Zeit, Watt), zeitlich sortiert."""
        out: list[tuple[datetime, float]] = []
        for key, value in self.watts.items():
            try:
                out.append((datetime.fromisoformat(key), float(value)))
            except ValueError:
                continue
        out.sort(key=lambda p: p[0])
        return out

    def power_at(self, moment: datetime) -> float | None:
        """Erwartete Leistung zu einem Zeitpunkt (linear interpoliert).

        forecast.solar liefert Stützstellen im 15-Minuten- bis Stundenraster;
        dazwischen wird interpoliert, damit ein Vergleich mit dem aktuellen
        Messwert überhaupt aussagekräftig ist."""
        points = self._points()
        if not points:
            return None
        moment = moment.replace(tzinfo=None) if moment.tzinfo else moment
        previous: tuple[datetime, float] | None = None
        for stamp, watts in points:
            naive = stamp.replace(tzinfo=None) if stamp.tzinfo else stamp
            if naive >= moment:
                if previous is None:
                    return watts
                p_time, p_watts = previous
                p_naive = p_time.replace(tzinfo=None) if p_time.tzinfo else p_time
                span = (naive - p_naive).total_seconds()
                if span <= 0:
                    return watts
                ratio = (moment - p_naive).total_seconds() / span
                return p_watts + (watts - p_watts) * ratio
            previous = (stamp, watts)
        return points[-1][1]

    def is_transient_dip(self, current_pv_w: float, now: datetime | None = None) -> bool:
        """Zieht gerade nur eine Wolke durch?

        True, wenn die Erzeugung deutlich unter der Prognose für jetzt liegt,
        die Prognose für die nächste halbe Stunde aber weiterhin auf einem
        Niveau ist, das die laufende Last wieder trägt. Dann lohnt es sich,
        eine Ladung zu halten: Ein Neustart kostet Start-Hysterese, einen
        Ladezyklus und damit mehr Überschuss, als das Halten an Netzstrom zieht.
        """
        if not self.available:
            return False
        now = now or local_now()
        expected_now = self.power_at(now)
        expected_soon = self.power_at(now + timedelta(minutes=RECOVERY_HORIZON_MIN))
        if not expected_now or not expected_soon:
            return False
        if expected_now < 500:  # nachts/Dämmerung – da gibt es nichts zu halten
            return False
        dipped = current_pv_w < expected_now * DIP_RATIO
        recovers = expected_soon >= expected_now * DIP_RATIO
        return dipped and recovers

    def expected_surplus_kwh(
        self, house_power_w: float, until: datetime | None = None, now: datetime | None = None
    ) -> float | None:
        """Erwarteter, für steuerbare Lasten nutzbarer Solarertrag bis `until`.

        Grobe, aber ehrliche Rechnung: erwartete Erzeugung minus dem, was der
        Haushalt in derselben Zeit voraussichtlich selbst verbraucht (aktuelle
        Hauslast als Näherung). Genau dafür reicht es – die Zielladung muss
        nur wissen, ob die Sonne *ungefähr* reicht."""
        if not self.available:
            return None
        now = now or local_now()
        until = until or datetime.combine(now.date() + timedelta(days=1), time(0, 0))
        points = [
            (stamp.replace(tzinfo=None) if stamp.tzinfo else stamp, watts)
            for stamp, watts in self._points()
        ]
        window = [(s, w) for s, w in points if now <= s <= until]
        if len(window) < 2:
            return 0.0
        energy_wh = 0.0
        for (t0, w0), (t1, w1) in zip(window, window[1:]):
            hours = (t1 - t0).total_seconds() / 3600.0
            if hours <= 0 or hours > 3:  # Lücken in den Daten nicht hochrechnen
                continue
            usable = max(0.0, (w0 + w1) / 2.0 - max(0.0, house_power_w))
            energy_wh += usable * hours
        return energy_wh / 1000.0

    def summary(self) -> dict:
        today = datetime.now(timezone.utc).astimezone().date()
        return {
            "provider": self.provider,
            "today_kwh": self.daily_kwh.get(today.isoformat()),
            "tomorrow_kwh": self.daily_kwh.get((today + timedelta(days=1)).isoformat()),
            "daily_kwh": self.daily_kwh,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "active": self.available,
            "now_w": self.power_at(local_now()) if self.available else None,
        }


__all__ = ["ForecastService", "date"]
