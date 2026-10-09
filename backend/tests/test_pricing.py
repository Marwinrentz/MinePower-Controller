"""Tests der Preislogik -- besonders der drei Faelle, an denen es klemmt:
negative Boersenpreise, Viertelstundenslots, Zeitumstellung.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.pricing import (
    PriceComponents,
    PriceSlot,
    PriceTable,
    evaluate_price,
)

BERLIN = ZoneInfo("Europe/Berlin")


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def table(pairs, minutes=60):
    return PriceTable([PriceSlot(t, p, minutes) for t, p in pairs])


# --------------------------------------------------------------- Endpreis


class TestPriceComponents:
    def test_spot_plus_everything(self):
        c = PriceComponents(grid_fees_ct=8.0, levies_ct=2.0, supplier_margin_ct=1.5, vat_pct=19.0)
        # (5 + 8 + 2 + 1.5) * 1.19
        assert c.apply(5.0) == pytest.approx(19.635)

    def test_fixed_part_is_price_at_zero_spot(self):
        c = PriceComponents(grid_fees_ct=10.0, vat_pct=19.0)
        assert c.fixed_ct == pytest.approx(11.9)

    def test_negative_spot_still_costs_money(self):
        """Der Endpreis bei -2 ct Boerse ist trotzdem positiv: Netzentgelt und
        Steuer bleiben. Genau deshalb wird die Negativ-Regel gegen den SPOT
        geprueft und nicht gegen den Endpreis."""
        c = PriceComponents(grid_fees_ct=10.0, vat_pct=19.0)
        assert c.apply(-2.0) > 0

    def test_from_dict_tolerates_junk(self):
        c = PriceComponents.from_dict({"grid_fees_ct": "8.5", "levies_ct": None, "vat_pct": ""})
        assert c.grid_fees_ct == 8.5
        assert c.levies_ct == 0.0
        assert c.vat_pct == 0.0


# --------------------------------------------------------------- Tabelle


class TestPriceTable:
    def test_price_within_slot(self):
        t = table([(utc(2026, 3, 1, 10), 12.0), (utc(2026, 3, 1, 11), 20.0)])
        assert t.at(utc(2026, 3, 1, 10, 30)) == 12.0
        assert t.at(utc(2026, 3, 1, 11, 5)) == 20.0

    def test_before_first_slot_is_unknown(self):
        t = table([(utc(2026, 3, 1, 10), 12.0)])
        assert t.at(utc(2026, 3, 1, 9)) is None

    def test_after_last_slot_is_unknown(self):
        """Der letzte Slot endet. Danach gilt NICHT sein Preis weiter -- sonst
        laedt die Anlage auf Basis eines Preises von gestern."""
        t = table([(utc(2026, 3, 1, 10), 12.0)])
        assert t.at(utc(2026, 3, 1, 11, 1)) is None

    def test_quarter_hour_slots(self):
        """EPEX liefert zunehmend Viertelstundenprodukte. Ein Parser, der stur
        60 Minuten annimmt, haelt den 10:00-Preis bis 11:00 fuer gueltig --
        drei Viertelstunden mit falschem Preis."""
        t = table(
            [(utc(2026, 3, 1, 10, m), p) for m, p in [(0, 5.0), (15, 30.0), (30, 5.0), (45, 30.0)]],
            minutes=15,
        )
        assert t.at(utc(2026, 3, 1, 10, 7)) == 5.0
        assert t.at(utc(2026, 3, 1, 10, 20)) == 30.0
        assert t.at(utc(2026, 3, 1, 10, 44)) == 5.0
        assert t.at(utc(2026, 3, 1, 10, 59)) == 30.0

    def test_naive_timestamp_treated_as_utc(self):
        t = table([(utc(2026, 3, 1, 10), 12.0)])
        assert t.at(datetime(2026, 3, 1, 10, 30)) == 12.0

    def test_slots_get_sorted(self):
        t = table([(utc(2026, 3, 1, 12), 30.0), (utc(2026, 3, 1, 10), 10.0)])
        assert [s.price_ct for s in t.slots] == [10.0, 30.0]

    def test_cheapest_in_window(self):
        t = table([(utc(2026, 3, 1, h), p) for h, p in [(10, 20.0), (11, 5.0), (12, 30.0)]])
        assert t.cheapest(utc(2026, 3, 1, 10)).price_ct == 5.0

    def test_empty_table(self):
        assert PriceTable().at(utc(2026, 3, 1, 10)) is None
        assert PriceTable().is_empty


class TestDaylightSaving:
    """Zeitumstellung. In Ortszeit gibt es im Oktober 02:00 zweimal und im
    Maerz gar nicht -- in UTC laeuft die Zeit durch. Diese Tests halten fest,
    dass die Tabelle das ueberlebt."""

    def test_autumn_doubled_hour_has_distinct_slots(self):
        # 2026-10-25: Umstellung 03:00 -> 02:00 Ortszeit. In UTC sind das die
        # Stunden 00:00 und 01:00 -- zwei verschiedene Slots mit eigenem Preis.
        t = table([(utc(2026, 10, 25, 0), 4.0), (utc(2026, 10, 25, 1), 9.0)])
        first = datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0)
        second = datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=1)
        assert t.at(first.astimezone(timezone.utc)) == 4.0
        assert t.at(second.astimezone(timezone.utc)) == 9.0

    def test_spring_missing_hour_does_not_create_gap(self):
        # 2026-03-29: 02:00 Ortszeit existiert nicht. Die UTC-Slots laufen
        # trotzdem lueckenlos durch.
        t = table([(utc(2026, 3, 29, h), 10.0 + h) for h in range(0, 4)])
        for h in range(0, 4):
            assert t.at(utc(2026, 3, 29, h, 30)) == 10.0 + h


# --------------------------------------------------------------- Entscheidung


class TestEvaluatePrice:
    def test_below_limit_charges(self):
        t = table([(utc(2026, 3, 1, 10), 5.0)])
        d = evaluate_price(t, utc(2026, 3, 1, 10, 5), limit_ct=22.0)
        assert d.should_charge
        assert "5.0 ct" in d.reason and "22.0 ct" in d.reason

    def test_above_limit_does_not_charge(self):
        t = table([(utc(2026, 3, 1, 10), 40.0)])
        d = evaluate_price(t, utc(2026, 3, 1, 10, 5), limit_ct=22.0)
        assert not d.should_charge
        assert not d.fell_back

    def test_limit_compares_against_final_price_not_spot(self):
        """Der eigentliche Zweck der Aufschlaege: 5 ct Boerse sind mit
        Netzentgelt und Steuer 19,6 ct. Gegen eine Schwelle von 15 ct
        geprueft heisst das NICHT laden -- ohne Aufschlaege haette der
        Vergleich 5 < 15 ergeben und die Anlage haette zum Endpreis von
        19,6 ct geladen."""
        t = table([(utc(2026, 3, 1, 10), 5.0)])
        c = PriceComponents(grid_fees_ct=8.0, levies_ct=2.0, supplier_margin_ct=1.5, vat_pct=19.0)
        d = evaluate_price(t, utc(2026, 3, 1, 10, 5), limit_ct=15.0, components=c)
        assert not d.should_charge
        assert d.price_ct == pytest.approx(19.635)

    def test_negative_spot_always_charges(self):
        """Auch wenn der Endpreis ueber der Schwelle liegt: Bei negativem
        Boersenpreis gibt es Geld fuers Abnehmen."""
        t = table([(utc(2026, 3, 1, 10), -3.0)])
        c = PriceComponents(grid_fees_ct=20.0, vat_pct=19.0)
        d = evaluate_price(t, utc(2026, 3, 1, 10, 5), limit_ct=5.0, components=c)
        assert d.should_charge
        assert "Negativ" in d.reason

    def test_negative_spot_can_be_disabled(self):
        t = table([(utc(2026, 3, 1, 10), -3.0)])
        c = PriceComponents(grid_fees_ct=20.0, vat_pct=19.0)
        d = evaluate_price(t, utc(2026, 3, 1, 10, 5), limit_ct=5.0, components=c,
                           charge_on_negative=False)
        assert not d.should_charge

    def test_no_data_falls_back_to_pv_only(self):
        """Fail-safe. Ohne Preisdaten nicht raten -- und vor allem nicht 0 ct
        annehmen, sonst laedt die Anlage bei jedem Ausfall des Tarifabrufs
        sofort aus dem Netz."""
        d = evaluate_price(PriceTable(), utc(2026, 3, 1, 10), limit_ct=22.0)
        assert not d.should_charge
        assert d.fell_back
        assert d.price_ct is None
        assert "Keine Preisdaten" in d.reason

    def test_stale_table_falls_back(self):
        """Tabelle vorhanden, aber der gefragte Zeitpunkt liegt dahinter."""
        t = table([(utc(2026, 3, 1, 10), 5.0)])
        d = evaluate_price(t, utc(2026, 3, 2, 10), limit_ct=22.0)
        assert d.fell_back

    def test_exactly_at_limit_charges(self):
        t = table([(utc(2026, 3, 1, 10), 22.0)])
        d = evaluate_price(t, utc(2026, 3, 1, 10, 5), limit_ct=22.0)
        assert d.should_charge


# --------------------------------------------------------------- Batterie


