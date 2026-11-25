"""Geräteformular: Geräte eines Cloud-Kontos antippen, mehrere = mehrere Einträge (3.5.2)."""
from __future__ import annotations

import re

from test_setup import _open_form

CHOICES = {"device_id": [
    {"value": "id-s", "label": "Daikin Schlafzimmer (dx4)"},
    {"value": "id-w", "label": "Daikin Wohnzimmer (dx4)"},
    {"value": "id-p", "label": "DaikinPizzo (dx4)"},
]}


def test_pick_two_devices_creates_two_entries(phone):
    ph = phone()
    page = ph.page
    created: list = []

    def test_route(route):
        route.fulfill(status=200, json={"ok": True, "message": "Verbindung erfolgreich", "values": {},
                                        "warnings": [], "write_test": None, "choices": CHOICES})

    def devices_route(route):
        if route.request.method == "POST":
            body = route.request.post_data_json
            created.append(body)
            return route.fulfill(status=200, json={"id": 100 + len(created), **body, "enabled": True})
        return route.fallback()

    page.route(re.compile(r".*/api/devices/test$"), test_route)
    page.route(re.compile(r".*/api/devices$"), devices_route)
    page = _open_form(ph, "daikin_onecta")
    for label, value in (("Client-ID", "cid"), ("Client-Secret", "geheim"), ("Refresh-Token", "rt")):
        page.locator(".field", has_text=label).locator("input").first.fill(value)
    assert page.locator("[data-testid=device-pick]").count() == 0  # erst nach dem Test
    ph.tap(page.get_by_role("button", name="Verbindung testen"))
    page.wait_for_timeout(400)
    picks = page.locator("[data-testid=device-pick]")
    assert picks.count() == 3
    ph.tap(picks.nth(1))
    page.wait_for_timeout(100)
    assert page.locator(".field", has_text="Bezeichnung").locator("input").first.input_value() == "Daikin Wohnzimmer (dx4)"
    ph.tap(picks.nth(2))
    page.wait_for_timeout(100)
    assert "2 Einträge" in page.locator(".field", has_text="Geräte im Konto").inner_text()
    ph.tap(page.get_by_role("button", name="Speichern"))
    page.wait_for_timeout(500)
    assert [(c["name"], c["config"]["device_id"]) for c in created] == [
        ("Daikin Wohnzimmer (dx4)", "id-w"), ("DaikinPizzo (dx4)", "id-p")]
    assert all(c["driver_id"] == "daikin_onecta" and c["config"]["refresh_token"] == "rt" for c in created)
