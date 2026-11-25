"""Geräteformular: Einrichtungsanleitung und Knopf 'Verbinden' (OAuth, 3.5)."""
from __future__ import annotations

import json
import re
from pathlib import Path

DRIVERS = json.loads((Path(__file__).parent / "fixtures" / "drivers_setup.json").read_text())


def _open_form(ph, driver_id: str):
    page = ph.page
    page.route(re.compile(r".*/api/drivers\?category=water_heater.*"),
               lambda r: r.fulfill(status=200, json=DRIVERS))
    page = ph.open("/einstellungen?tab=devices")
    ph.tap(page.get_by_role("button", name=re.compile("Wärme")).first)
    page.locator("select").first.select_option(driver_id)
    page.wait_for_timeout(300)
    return page


def test_guide_with_filled_link(phone):
    ph = phone()
    page = _open_form(ph, "homey_pro")
    steps = page.locator(".setup-steps > li")
    assert steps.count() == 5
    assert "API-Schlüssel" in steps.nth(1).inner_text()
    link = page.locator(".setup-link", has_text="my.homey.app")
    assert link.get_attribute("href") == "https://my.homey.app"
    assert page.locator(".oauth-connect").count() == 0  # Homey: Schlüssel, kein OAuth


def test_connect_with_pasted_address_fills_token(phone):
    ph = phone()
    page = ph.page
    calls: list = []

    def start(route):
        calls.append(("start", route.request.post_data_json))
        route.fulfill(status=200, json={"auth_url": "about:blank#login", "state": "st-1"})

    def finish(route):
        calls.append(("finish", route.request.post_data_json))
        route.fulfill(status=200, json={"field": "refresh_token", "refresh_token": "rt-neu",
                                        "message": "Mit Daikin verbunden – jetzt 'Verbindung testen' und speichern"})

    page.route(re.compile(r".*/api/oauth/start$"), start)
    page.route(re.compile(r".*/api/oauth/finish$"), finish)
    page = _open_form(ph, "daikin_onecta")
    button = page.get_by_role("button", name="Mit Daikin verbinden")
    assert button.is_disabled()  # ohne Client-ID und Secret
    page.locator(".field", has_text="Client-ID").locator("input").first.fill("cid")
    page.locator(".field", has_text="Client-Secret").locator("input").first.fill("geheim")
    redirect = page.locator(".oauth-connect .field", has_text="Redirect-URI").locator("input")
    redirect.fill("https://localhost/oauth/callback")
    assert button.is_disabled() and page.locator(".oauth-connect .banner").count() == 1  # localhost abgelehnt
    redirect.fill("https://192.0.2.10:8000/oauth/callback")
    ph.tap(button)
    page.wait_for_timeout(400)
    for extra in ph.ctx.pages[1:]:
        extra.close()
    assert calls[0] == ("start", {"provider": "daikin_onecta", "client_id": "cid", "client_secret": "geheim",
                                  "redirect_uri": "https://192.0.2.10:8000/oauth/callback", "device_id": None})
    page.locator(".oauth-connect .field", has_text="Adresse nach der Anmeldung").locator("input").fill(
        "https://192.0.2.10:8000/oauth/callback?code=abc&state=st-1")
    ph.tap(page.get_by_role("button", name="Übernehmen"))
    page.wait_for_timeout(400)
    assert calls[1][1]["state"] == "st-1" and "code=abc" in calls[1][1]["code_or_url"]
    assert page.locator(".field", has_text="Refresh-Token").locator("input").first.input_value() == "rt-neu"
    assert "Verbunden" in page.locator(".oauth-connect").inner_text()


def test_callback_page_hands_code_to_form(phone):
    ph = phone()
    page = ph.open("/oauth/callback?code=abc&state=st-1")
    assert "Anmeldung erfolgreich" in page.inner_text("body")
    page = ph.open("/oauth/callback?error=access_denied&state=st-1")
    assert "Anmeldung abgebrochen" in page.inner_text("body") and "access_denied" in page.inner_text("body")
