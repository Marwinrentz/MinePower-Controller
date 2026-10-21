"""Dynamische Stromtarife: Tibber & aWATTar (EPEX-Spot).

Preise werden stündlich aktualisiert und dem Control-Loop als
`current_price_ct()` / `is_cheap_hour()` bereitgestellt (Modus 'price').
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timezone

import aiohttp
from sqlalchemy import select

from ..core.pricing import AutoLimit, PriceComponents, PriceSlot, PriceTable, auto_limit, evaluate_price
from ..db import async_session
from ..models import Setting

from ..core.secrets import reveal_settings

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
        #: Aufschlaege zwischen Boersen- und Endpreis (siehe core/pricing.py).
        self.components = PriceComponents()
        self.table = PriceTable()
        self.charge_on_negative = True
        #: Automatische Grenze: immer der tiefste Preis der naechsten 24 h.
        self.auto_limit = False
        self.auto_window_h = 2.0
        self.auto_tolerance_ct = 0.0
        self._task: asyncio.Task | None = None
        #: Weckruf nach geaenderten Einstellungen (siehe request_reload).
        self._wake = asyncio.Event()
        #: Letzter Abruffehler (Klartext) und Zeitpunkt – für die Diagnose.
        self.last_error: str | None = None
        self.last_attempt: datetime | None = None

    @property
    def prices(self) -> list[tuple[datetime, float]]:
        """Rueckwaertskompatible Sicht fuer aelteren Code und Tests."""
        return [(s.start, s.price_ct) for s in self.table.slots]

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

    def request_reload(self) -> None:
        """Einstellungen sofort neu lesen (nach "Speichern"), nicht erst beim
        naechsten Takt in bis zu 30 Minuten. Preise werden dabei nur neu
        geholt, wenn sich Anbieter, Zugang oder Region geaendert haben."""
        self._wake.set()

    def _source_key(self) -> tuple:
        return (self.provider, self.config.get("tibber_token"), self.config.get("awattar_region"))

    async def _run(self) -> None:
        failures = 0
        woken = False
        last_key: tuple | None = None
        while True:
            self._wake.clear()
            try:
                await self._load_config()
                key = self._source_key()
                source_changed = key != last_key
                last_key = key
                # Nach einem Weckruf nur dann ins Netz, wenn die Quelle neu ist
                # oder noch keine Preise vorliegen -- sonst genuegt das Neulesen.
                reread_only = woken and not source_changed and not self.table.is_empty
                if not reread_only:
                    if self.provider != "none":
                        self.last_attempt = datetime.now(timezone.utc)
                        await self._refresh()
                        if self.table.at(datetime.now(timezone.utc)) is None:
                            raise RuntimeError("Anbieter lieferte keinen Preis für die aktuelle Stunde")
                    self.last_error = None
                    failures = 0
            except Exception as exc:  # noqa: BLE001
                failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                # Nur die ersten Fehler und dann gelegentlich loggen.
                if failures <= 2 or failures % 10 == 0:
                    log.warning("Tarif-Update fehlgeschlagen (%d×): %s", failures, exc)
            # Erfolgreich: alle 30 min (Stundenpreise ändern sich nicht).
            # Fehlgeschlagen: früh wiederholen (1, 2, 4 … bis 30 min) –
            # vorher wartete ein einziger Aussetzer beim Start 30 Minuten,
            # in denen ohne Preis auch nicht preisoptimiert geladen wurde.
            delay = 1800 if failures == 0 else min(1800, 60 * 2 ** min(failures - 1, 5))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
                woken = True
            except asyncio.TimeoutError:
                woken = False

    async def _load_config(self) -> None:
        async with async_session() as session:
            row = await session.scalar(select(Setting).where(Setting.key == "tariff"))
        cfg = reveal_settings("tariff", (row.value if row else {}) or {})
        self.provider = cfg.get("provider", "none")
        # 'grid_charge_limit_ct" ist der neue Name; 'cheap_limit_ct" bleibt als
        # Rueckfall, damit bestehende Installationen ihre Schwelle behalten.
        self.cheap_limit_ct = float(
            cfg.get("grid_charge_limit_ct", cfg.get("cheap_limit_ct", 15)) or 15
        )
        self.components = PriceComponents.from_dict(cfg)
        self.charge_on_negative = bool(cfg.get("charge_on_negative", True))
        self.auto_limit = bool(cfg.get("auto_limit", False))
        self.auto_window_h = _number(cfg.get("auto_limit_window_h"), 2.0, 0.0, 12.0)
        self.auto_tolerance_ct = _number(cfg.get("auto_limit_tolerance_ct"), 0.0, 0.0, 20.0)
        self.config = cfg

    async def _refresh(self) -> None:
        if self.provider == "tibber":
            await self._fetch_tibber()
        elif self.provider == "awattar":
            await self._fetch_awattar()

    async def _fetch_tibber(self) -> None:
        token = self.config.get("tibber_token", "")
        if not token:
            raise RuntimeError("Tibber-Token fehlt oder ist nicht lesbar – unter Tarif neu eintragen")
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
        slots = []
        for entry in (info.get("today") or []) + (info.get("tomorrow") or []):
            start = datetime.fromisoformat(entry["startsAt"]).astimezone(timezone.utc)
            slots.append(PriceSlot(start, float(entry["total"]) * 100.0, 60))  # € -> ct
        self.table = PriceTable(slots, fetched_at=datetime.now(timezone.utc))
        log.info("Tibber: %d Stundenpreise geladen", len(slots))

    async def _fetch_awattar(self) -> None:
        url = AWATTAR_URLS.get(self.config.get("awattar_region", "de"), AWATTAR_URLS["de"])
        async with aiohttp.ClientSession() as http:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                resp.raise_for_status()
                data = await resp.json()
        slots = []
        for e in data.get("data", []):
            start = datetime.fromtimestamp(e["start_timestamp"] / 1000, tz=timezone.utc)
            end = datetime.fromtimestamp(e["end_timestamp"] / 1000, tz=timezone.utc)
            # Laenge aus dem Datensatz ableiten statt 60 Minuten anzunehmen:
            # aWATTar liefert inzwischen auch Viertelstundenprodukte, und ein
            # als Stunde gedeuteter 15-min-Slot haelt seinen Preis drei
            # Viertelstunden zu lange fuer gueltig.
            minutes = max(1, round((end - start).total_seconds() / 60))
            # Boersenpreis roh speichern (EUR/MWh -> ct/kWh). Die Aufschlaege
            # kommen erst bei der Abfrage dazu, damit eine Aenderung in den
            # Einstellungen sofort wirkt und nicht erst beim naechsten Abruf.
            slots.append(PriceSlot(start, e["marketprice"] / 10.0, minutes))
        self.table = PriceTable(slots, fetched_at=datetime.now(timezone.utc))
        log.info("aWATTar: %d Preisslots geladen", len(slots))

    # ------------------------------------------------------------ Abfragen

    @property
    def effective_components(self) -> PriceComponents:
        """Aufschlaege, die auf den gelieferten Preis noch draufkommen.

        **Tibber liefert bereits den Endpreis** (`total` enthaelt Netzentgelt,
        Umlagen und Steuer). Die Aufschlaege hier noch einmal daraufzurechnen
        hiesse, sie doppelt zu zahlen -- im Zweifel 20 ct zu viel, und die
        Ladeschwelle greift nie. aWATTar dagegen liefert den nackten
        EPEX-Spotpreis, dort sind sie noetig.
        """
        if self.provider == "tibber":
            return PriceComponents()
        return self.components

    def current_price_ct(self) -> float | None:
        """Der **Endpreis**, nicht der nackte Boersenpreis. Alles im System
        vergleicht gegen diesen Wert -- ein Boersenpreis von 5 ct ist mit
        Netzentgelt und Steuer rund 20 ct, und eine Schwelle, die gegen 5 ct
        prueft, laedt zum Endpreis von 20 ct."""
        spot = self.table.at(datetime.now(timezone.utc))
        return None if spot is None else self.effective_components.apply(spot)

    def spot_price_ct(self) -> float | None:
        """Der reine Boersenpreis -- fuer die Anzeige und die Negativ-Regel."""
        return self.table.at(datetime.now(timezone.utc))

    def auto_status(self) -> AutoLimit | None:
        """Die automatische Grenze, falls eingeschaltet und berechenbar."""
        if not self.auto_limit:
            return None
        return auto_limit(
            self.table, datetime.now(timezone.utc), self.effective_components,
            min_window_h=self.auto_window_h, tolerance_ct=self.auto_tolerance_ct,
        )

    def effective_limit_ct(self) -> float:
        """Die Grenze, die gerade gilt: die automatische (tiefster Preis der
        naechsten 24 h) oder die feste. Fehlen Preise fuer die Vorschau, gilt
        die feste -- eine Automatik ohne Daten darf nicht raten."""
        auto = self.auto_status()
        return auto.limit_ct if auto else self.cheap_limit_ct

    def decide(self):
        """Volle Preisentscheidung mit Begruendung (siehe core/pricing.py)."""
        auto = self.auto_status()
        limit = auto.limit_ct if auto else self.cheap_limit_ct
        result = evaluate_price(
            self.table, datetime.now(timezone.utc), limit,
            self.effective_components, charge_on_negative=self.charge_on_negative,
        )
        if auto and not result.fell_back:
            result = dataclasses.replace(result, reason=result.reason + " (automatisch: Tiefstpreis)")
        return result

    def is_cheap_hour(self) -> bool:
        return self.decide().should_charge

    def status(self) -> dict:
        """Zustand der Preisquelle für die Diagnose: Ohne gültigen Preis wird
        nie aus dem Netz geladen (siehe PriceTable.at) – ob das gerade der
        Fall ist, muss man sehen können."""
        now = datetime.now(timezone.utc)
        covered = max((slot.end for slot in self.table.slots), default=None)
        auto = self.auto_status()
        return {
            "provider": self.provider,
            "limit_ct": self.effective_limit_ct(),
            "fixed_limit_ct": self.cheap_limit_ct,
            "auto_limit": self.auto_limit,
            "auto_active": auto is not None,
            "auto_lowest_ct": round(auto.lowest_ct, 2) if auto else None,
            "auto_window_h": round(auto.window_h, 2) if auto else None,
            "price_now_ct": self.current_price_ct(),
            "spot_now_ct": self.spot_price_ct(),
            "fetched_at": self.table.fetched_at.isoformat() if self.table.fetched_at else None,
            "covered_until": covered.isoformat() if covered else None,
            "has_current_price": self.table.at(now) is not None,
            "last_error": self.last_error,
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
            "components": dataclasses.asdict(self.components),
        }

    def upcoming(self) -> list[dict]:
        """Kommende Slots fuer die Preiskurve im GUI -- mit Endpreis, damit
        die Kurve dieselbe Skala hat wie die eingestellte Schwelle."""
        now = datetime.now(timezone.utc)
        comp = self.effective_components
        return [
            {
                "start": slot.start.isoformat(),
                "minutes": slot.minutes,
                "spot_ct": round(slot.price_ct, 2),
                "price_ct": round(comp.apply(slot.price_ct), 2),
            }
            for slot in self.table.upcoming(now, hours=36)
        ]


def _number(value, default: float, low: float, high: float) -> float:
    """Zahl aus den Einstellungen, auf [low, high] begrenzt; Muell -> Standard."""
    try:
        number = float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, number))
