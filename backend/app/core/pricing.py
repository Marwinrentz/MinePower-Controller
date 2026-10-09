"""Strompreise: vom Boersenpreis zum Endpreis, und die Frage 'laden oder nicht".

Bewusst ohne jede Abhaengigkeit zu Datenbank, Netz oder Uhr: Alles hier sind
reine Funktionen ueber uebergebene Werte. Das ist der Grund, warum es diese
Datei ueberhaupt gibt -- die Preisentscheidung war vorher ueber
`services/tariff.py` und drei Zweige in `regulation.py` verteilt und liess
sich nur im Gesamtsystem pruefen.

Die drei Faelle, an denen naive Implementierungen scheitern, stehen hier als
Testfaelle fest: negative Boersenpreise, Viertelstundenslots und die
Zeitumstellung.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class PriceComponents:
    """Was zwischen Boersenpreis und dem liegt, was man wirklich zahlt.

    Ein Boersenpreis von 3 ct/kWh heisst nicht, dass die Kilowattstunde 3 ct
    kostet. Netzentgelt, Abgaben, Steuer und die Marge des Versorgers kommen
    obendrauf und machen in Deutschland typisch 15-25 ct aus. Wer die
    Ladeschwelle gegen den nackten Boersenpreis vergleicht, laedt entweder nie
    (Schwelle zu niedrig gesetzt) oder dauernd (Schwelle auf Endpreisniveau,
    aber Vergleich gegen den Boersenpreis).

    Alle Werte in ct/kWh, `vat_pct` in Prozent.
    """

    #: Netzentgelt, Messstellenentgelt, Konzessionsabgabe
    grid_fees_ct: float = 0.0
    #: Stromsteuer, Umlagen
    levies_ct: float = 0.0
    #: Aufschlag des Versorgers auf den Boersenpreis
    supplier_margin_ct: float = 0.0
    #: Umsatzsteuer in Prozent, wird zum Schluss auf die Summe geschlagen
    vat_pct: float = 0.0

    def apply(self, spot_ct: float) -> float:
        """Boersenpreis -> Endpreis."""
        net = spot_ct + self.grid_fees_ct + self.levies_ct + self.supplier_margin_ct
        return net * (1.0 + self.vat_pct / 100.0)

    @property
    def fixed_ct(self) -> float:
        """Was unabhaengig vom Boersenpreis anfaellt (inkl. USt)."""
        return self.apply(0.0)

    @classmethod
    def from_dict(cls, cfg: dict) -> "PriceComponents":
        def num(key: str, default: float = 0.0) -> float:
            try:
                return float(cfg.get(key, default) or 0.0)
            except (TypeError, ValueError):
                return default

        return cls(
            grid_fees_ct=num("grid_fees_ct"),
            levies_ct=num("levies_ct"),
            supplier_margin_ct=num("supplier_margin_ct"),
            vat_pct=num("vat_pct"),
        )


@dataclass
class PriceSlot:
    """Ein Preiszeitraum. `start` ist immer zeitzonenbewusst (UTC)."""

    start: datetime
    price_ct: float
    #: Laenge des Slots. Stundenprodukte 60 min, EPEX-Viertelstunden 15 min.
    minutes: int = 60

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)


@dataclass
class PriceTable:
    """Die bekannten Preise, sortiert und lueckenlos abfragbar.

    **Warum UTC durchgehend.** Preisfenster ueber die Zeitumstellung hinweg
    sind der Klassiker: In der Nacht auf den letzten Oktobersonntag gibt es
    02:00 Ortszeit zweimal, im Maerz gar nicht. Wer in Ortszeit rechnet,
    bekommt dort entweder doppelte Slots oder eine Luecke von einer Stunde --
    und laedt dann entweder doppelt oder gar nicht. In UTC existiert das
    Problem nicht; umgerechnet wird erst fuer die Anzeige.
    """

    slots: list[PriceSlot] = field(default_factory=list)
    #: Wann die Tabelle zuletzt erfolgreich befuellt wurde.
    fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        self.slots.sort(key=lambda s: s.start)
        self._starts = [s.start for s in self.slots]

    def at(self, when: datetime) -> float | None:
        """Preis zum Zeitpunkt, oder None wenn nicht abgedeckt.

        Bewusst None statt 0.0 bei fehlenden Daten: 0 ct waere der guenstigste
        denkbare Preis und wuerde sofortiges Netzladen ausloesen. Ein
        abgelaufener Tarifabruf darf nicht dazu fuehren, dass die Anlage zum
        Hoechstpreis laedt.
        """
        if not self.slots:
            return None
        when = _as_utc(when)
        index = bisect_right(self._starts, when) - 1
        if index < 0:
            return None
        slot = self.slots[index]
        return slot.price_ct if when < slot.end else None

    def upcoming(self, when: datetime, hours: int = 24) -> list[PriceSlot]:
        when = _as_utc(when)
        horizon = when + timedelta(hours=hours)
        return [s for s in self.slots if s.end > when and s.start < horizon]

    def cheapest(self, when: datetime, hours: int = 24) -> PriceSlot | None:
        window = self.upcoming(when, hours)
        return min(window, key=lambda s: s.price_ct) if window else None

    @property
    def is_empty(self) -> bool:
        return not self.slots


def _as_utc(when: datetime) -> datetime:
    """Naive Zeitstempel als UTC deuten statt als Ortszeit.

    Ein naiver Zeitstempel aus `datetime.now()` waere Ortszeit und wuerde
    gegen UTC-Slots je nach Jahreszeit um ein bis zwei Stunden verrutschen --
    der Regler laedt dann eine Stunde zu frueh oder zu spaet. Wer hier
    ankommt, hat oben etwas vergessen; die Annahme UTC ist die, die zum Rest
    des Systems passt.
    """
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class PriceDecision:
    """Ergebnis der Preispruefung -- inklusive Begruendung im Klartext.

    Die Begruendung ist kein Beiwerk: Sie landet direkt im Dashboard. 'Preis
    18,2 ct unter Grenze 22,0 ct" beantwortet die Frage, warum gerade geladen
    wird, ohne dass jemand die Einstellungen aufschlagen muss.
    """

    should_charge: bool
    reason: str
    price_ct: float | None
    limit_ct: float
    #: True, wenn mangels Daten auf reines PV-Laden zurueckgefallen wurde.
    fell_back: bool = False


def evaluate_price(
    table: PriceTable,
    when: datetime,
    limit_ct: float,
    components: PriceComponents | None = None,
    *,
    charge_on_negative: bool = True,
) -> PriceDecision:
    """Soll jetzt aus dem Netz geladen werden?

    `limit_ct` wird gegen den **Endpreis** verglichen, nicht gegen den
    Boersenpreis -- siehe PriceComponents.
    """
    components = components or PriceComponents()
    spot = table.at(when)

    if spot is None:
        # Fail-safe: Ohne Preisdaten nicht raten. Reines PV-Laden ist der
        # Zustand, der nie Geld kostet.
        return PriceDecision(
            should_charge=False,
            reason="Keine Preisdaten – nur PV-Ueberschuss",
            price_ct=None,
            limit_ct=limit_ct,
            fell_back=True,
        )

    final = components.apply(spot)

    # Negative Boersenpreise: Es gibt Geld fuers Abnehmen. Der Endpreis kann
    # trotzdem positiv sein, weil Netzentgelte und Steuern bleiben -- deshalb
    # wird hier der SPOT-Preis geprueft, nicht der Endpreis. Wer bei -2 ct
    # Boerse nicht laedt, verschenkt die guenstigste Stunde des Jahres.
    if charge_on_negative and spot < 0:
        return PriceDecision(
            True,
            f"Negativer Boersenpreis ({spot:.1f} ct) – Netzladen lohnt sich immer",
            final, limit_ct,
        )

    if final <= limit_ct:
        return PriceDecision(
            True,
            f"Preis {final:.1f} ct unter Grenze {limit_ct:.1f} ct",
            final, limit_ct,
        )
    return PriceDecision(
        False,
        f"Preis {final:.1f} ct ueber Grenze {limit_ct:.1f} ct",
        final, limit_ct,
    )
