"""Automatische Preisgrenze: immer der tiefste Preis der naechsten 24 Stunden."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.pricing import (
    AUTO_MIN_COVERAGE_H,
    PriceComponents,
    PriceSlot,
    PriceTable,
    auto_limit,
    evaluate_price,
)
from app.services.tariff import TariffService


def utc(d, h, mi=0):
    return datetime(2026, 10, d, h, tzinfo=timezone.utc) + timedelta(minutes=mi)


def hourly(prices, start_hour=0, day=20):
    """Stundenpreise ab `start_hour` (UTC)."""
    return PriceTable([PriceSlot(utc(day, start_hour) + timedelta(hours=i), p, 60) for i, p in enumerate(prices)])


DAY = [20, 19, 18, 12, 9, 8, 9, 14, 22, 25, 24, 23, 21, 20, 19, 18, 21, 28, 30, 27, 24, 22, 21, 20]


class TestAutoLimit:
    def test_limit_is_lowest_price_with_no_window(self):
        result = auto_limit(hourly(DAY), utc(20, 0), min_window_h=0)
        assert result.limit_ct == 8
        assert result.lowest_ct == 8
        assert result.window_h == 1.0

    def test_same_result_at_any_absolute_price_level(self):
        """Egal welches Preisniveau: die Grenze liegt immer auf dem Tiefstwert."""
        cheap = auto_limit(hourly([p / 10 for p in DAY]), utc(20, 0), min_window_h=0)
        dear = auto_limit(hourly([p * 10 for p in DAY]), utc(20, 0), min_window_h=0)
        assert cheap.limit_ct == pytest.approx(0.8)
        assert dear.limit_ct == pytest.approx(80)

    def test_minimum_window_raises_limit_until_long_enough(self):
        result = auto_limit(hourly(DAY), utc(20, 0), min_window_h=3)
        # 8, 9, 9 zusammen 3 h -> Grenze 9
        assert result.limit_ct == 9
        assert result.window_h == 3.0

    def test_ties_all_join_the_window(self):
        result = auto_limit(hourly([10, 10, 10, 30, 30, 30, 30, 30]), utc(20, 0), min_window_h=1)
        assert result.limit_ct == 10
        assert result.window_h == 3.0

    def test_tolerance_widens_the_window(self):
        result = auto_limit(hourly(DAY), utc(20, 0), min_window_h=0, tolerance_ct=1.0)
        assert result.limit_ct == 9
        assert result.window_h == 3.0

    def test_components_are_applied_before_comparing(self):
        comp = PriceComponents(grid_fees_ct=10, vat_pct=0)
        assert auto_limit(hourly(DAY), utc(20, 0), comp, min_window_h=0).limit_ct == 18

    def test_quarter_hours_count_by_duration(self):
        slots = [PriceSlot(utc(20, 0, 15 * i), p, 15) for i, p in enumerate([9, 5, 5, 20] * 12)]
        result = auto_limit(PriceTable(slots), utc(20, 0), min_window_h=1)
        # Viertelstunden: Es braucht vier Slots fuer eine Stunde; 5 ct gibt es nur 24 Mal,
        # also reicht schon das Tiefstniveau.
        assert result.limit_ct == 5
        assert result.window_h >= 1

    def test_running_slot_counts_only_remaining_minutes(self):
        table = hourly([5, 9, 30, 30, 30, 30, 30, 30])
        assert auto_limit(table, utc(20, 0, 45), min_window_h=0.5).limit_ct == 9  # 15 min Rest < 30 min

    def test_past_prices_are_ignored(self):
        """Der tiefe Preis von heute frueh zaehlt nach seinem Ende nicht mehr."""
        table = hourly(DAY + [15] * 12)
        assert auto_limit(table, utc(20, 10), min_window_h=0).limit_ct == 15

    def test_no_prices_or_short_preview_returns_none(self):
        assert auto_limit(PriceTable(), utc(20, 0)) is None
        short = hourly([5, 6, 7, 8, 9])
        assert (short.upcoming(utc(20, 0))[-1].end - utc(20, 0)).total_seconds() / 3600 < AUTO_MIN_COVERAGE_H
        assert auto_limit(short, utc(20, 0)) is None

    def test_window_longer_than_known_time_uses_everything_known(self):
        result = auto_limit(hourly([5, 9, 7, 8, 6, 12, 11]), utc(20, 0), min_window_h=12)
        assert result.limit_ct == 12

    def test_charges_exactly_in_the_cheapest_hour(self):
        table = hourly(DAY)
        limit = auto_limit(table, utc(20, 0), min_window_h=0).limit_ct
        yes = evaluate_price(table, utc(20, 5, 30), limit)
        no = evaluate_price(table, utc(20, 6, 30), limit)
        assert not evaluate_price(table, utc(20, 4, 30), limit).should_charge
        assert yes.should_charge
        assert not no.should_charge


class TestTariffService:
    def make(self, auto=True, **kw):
        svc = TariffService()
        svc.provider = "awattar"
        svc.cheap_limit_ct = 15.0
        svc.auto_limit = auto
        svc.auto_window_h = kw.get("window", 0.0)
        svc.auto_tolerance_ct = kw.get("tol", 0.0)
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        # Spotpreise ct/kWh: heute Nacht 3 ct, sonst 20-30
        prices = [25, 24, 3, 22, 28, 30] + [26] * 24
        svc.table = PriceTable([PriceSlot(now + timedelta(hours=i), p, 60) for i, p in enumerate(prices)])
        return svc, prices

    def test_off_keeps_the_fixed_limit(self):
        svc, _ = self.make(auto=False)
        assert svc.effective_limit_ct() == 15.0
        assert svc.decide().limit_ct == 15.0
        assert svc.status()["auto_active"] is False

    def test_on_uses_lowest_price(self):
        svc, _ = self.make()
        assert svc.effective_limit_ct() == 3
        assert svc.status()["limit_ct"] == 3
        assert svc.status()["fixed_limit_ct"] == 15.0
        assert svc.status()["auto_lowest_ct"] == 3

    def test_decision_mentions_automatic(self):
        svc, _ = self.make()
        result = svc.decide()
        assert not result.should_charge  # aktuelle Stunde 25 ct
        assert "automatisch" in result.reason

    def test_without_prices_falls_back_to_fixed_limit(self):
        svc, _ = self.make()
        svc.table = PriceTable()
        assert svc.effective_limit_ct() == 15.0
        assert svc.status()["auto_active"] is False
        assert svc.decide().fell_back

    def test_negative_spot_still_charges(self):
        svc, _ = self.make()
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        svc.table = PriceTable([PriceSlot(now + timedelta(hours=i), p, 60) for i, p in enumerate([-2] + [25] * 12)])
        assert svc.decide().should_charge

    @pytest.mark.parametrize("raw,expected", [(None, 2.0), ("", 2.0), ("abc", 2.0), (-5, 0.0), (99, 12.0), ("3.5", 3.5)])
    def test_window_setting_is_clamped(self, raw, expected):
        from app.services.tariff import _number
        assert _number(raw, 2.0, 0.0, 12.0) == expected
