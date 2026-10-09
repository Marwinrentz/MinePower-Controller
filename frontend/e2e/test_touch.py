"""Touch-Bedienung am Handy: Scrollen löst nie etwas aus, bewusste Bedienung
funktioniert, riskante Aktionen fragen nach, jede Änderung ist rückgängig
zu machen. Läuft in Chromium (echte Touch-Events) und WebKit.
"""
from __future__ import annotations

import json


def device_actions(ph, action: str | None = None):
    out = []
    for method, path, body in ph.actions():
        if path.endswith("/action"):
            if action is None or (isinstance(body, dict) and body.get("action") == action):
                out.append(body)
    return out


def sheet_open(page) -> bool:
    return page.locator(".msheet").count() > 0


# ---------------------------------------------------------------- Wischen

def test_swiping_over_home_controls_triggers_nothing(phone):
    """Übersicht: Schnellzugriff-Karten und die Daumenleiste unten
    (Boost, Sofort laden, Auto aus) – Wischen löst nie etwas aus."""
    ph = phone()
    page = ph.open("/")
    for sel in (".action-bar .btn >> nth=0", ".action-bar .btn >> nth=1", ".action-bar .btn >> nth=2",
                ".quick-car .segment button >> nth=1", ".quick-water .btn.primary"):
        ph.swipe_over(page.locator(sel), 0, -160)
        ph.swipe_over(page.locator(sel), 6, 140)
    page.wait_for_timeout(500)
    assert page.locator(".msheet").count() == 0
    assert ph.actions() == []


def test_swiping_over_value_controls_changes_nothing(phone):
    """Auto-Seite: Ladegrenze (Stepper + Wertleiste) und Betriebsart."""
    ph = phone()
    page = ph.open("/auto")
    value = page.locator(".tval-value").first
    value.scroll_into_view_if_needed()
    before = value.inner_text()
    for sel in (".tval-step >> nth=0", ".tval-step >> nth=1", ".tval-bar", ".segment-lg button >> nth=2"):
        ph.swipe_over(page.locator(sel).first, 0, -120)
        ph.swipe_over(page.locator(sel).first, 8, 120)   # leicht schräg
    page.wait_for_timeout(1300)                          # länger als die Sammelpause der Stepper
    assert page.locator(".tval-value").first.inner_text() == before
    assert not sheet_open(page)
    assert ph.actions() == []


def test_scroll_really_scrolls_over_controls(phone, browser):
    """Chromium: Die Wischgeste über einem Regler scrollt die Seite (Scrollen hat Vorrang)."""
    if browser.browser_type.name != "chromium":
        return
    ph = phone()
    page = ph.open("/warmwasser")
    bar = page.locator(".tval-bar").first
    bar.scroll_into_view_if_needed()
    y0 = page.evaluate("scrollY")
    box = bar.bounding_box()
    ph.swipe(box["x"] + box["width"] / 2, box["y"] + 20, box["x"] + box["width"] / 2, box["y"] - 200)
    assert page.evaluate("scrollY") > y0 + 60
    assert ph.actions() == []


def open_target_sheet(ph):
    page = ph.open("/warmwasser")
    ph.tap(page.locator(".tval-bar").first)            # 'Wasser halten bei"
    page.wait_for_timeout(500)
    return page


def test_range_in_sheet_ignores_vertical_and_tap(phone):
    ph = phone()
    page = open_target_sheet(ph)
    rng = page.locator(".trange")
    big = page.locator(".tval-big")
    before = big.inner_text()
    ph.swipe_over(rng, 0, -90)
    ph.swipe_over(rng, 14, 110)       # schräg, überwiegend senkrecht
    ph.tap(rng)                       # Tipp allein ändert nichts
    assert big.inner_text() == before
    assert ph.actions() == []


# ---------------------------------------------------------------- bewusst bedienen

def test_horizontal_drag_commits_on_release_with_undo(phone):
    ph = phone()
    page = open_target_sheet(ph)
    rng = page.locator(".trange")
    box = rng.bounding_box()
    y = box["y"] + 30
    ph.swipe(box["x"] + box["width"] * 0.5, y, box["x"] + box["width"] * 0.5 + 90, y)
    saves = [w for w in ph.actions() if w[0] == "PATCH"]
    assert len(saves) == 1, ph.actions()
    new = saves[0][2]["settings"]["target_temp_c"]
    assert new > 60
    undo = page.get_by_role("button", name="Rückgängig")
    assert undo.is_visible()
    ph.tap(undo)
    saves = [w for w in ph.actions() if w[0] == "PATCH"]
    assert len(saves) == 2 and saves[1][2]["settings"]["target_temp_c"] == 60


def test_stepper_taps_are_collected_then_sent_once(phone):
    ph = phone()
    page = ph.open("/auto")
    plus = page.locator(".tval-step").nth(1)
    plus.scroll_into_view_if_needed()
    ph.tap(plus)
    ph.tap(plus)
    assert device_actions(ph, "set_charge_limit") == []          # noch nichts gesendet
    page.wait_for_timeout(1300)
    sent = device_actions(ph, "set_charge_limit")
    assert len(sent) == 1 and sent[0]["value"] == 82          # Schrittweite 1 %
    ph.tap(page.get_by_role("button", name="Rückgängig"))
    sent = device_actions(ph, "set_charge_limit")
    assert len(sent) == 2 and sent[1]["value"] == 80


# ---------------------------------------------------------------- riskante Aktionen

def test_boost_in_thumb_bar_needs_confirmation(phone):
    ph = phone()
    page = ph.open("/")
    boost = page.locator(".action-bar .btn").nth(0)
    ph.tap(boost)
    assert page.locator(".confirm-title").inner_text() == "Boost starten?"
    assert device_actions(ph, "boost") == []
    ph.tap(page.get_by_role("button", name="Abbrechen"))
    assert device_actions(ph, "boost") == []
    ph.tap(boost)
    ph.tap(page.locator("[data-confirm=yes]"))
    assert len(device_actions(ph, "boost")) == 1


def test_boost_on_water_page_needs_confirmation(phone):
    ph = phone()
    page = ph.open("/warmwasser")
    ph.tap(page.locator(".boost-xl .btn").first)
    assert page.locator(".confirm-title").is_visible()
    assert device_actions(ph, "boost") == []
    ph.tap(page.locator("[data-confirm=yes]"))
    assert len(device_actions(ph, "boost")) == 1


def test_fast_charge_needs_confirmation(phone):
    ph = phone(scenario="car_asleep")
    page = ph.open("/")
    ph.tap(page.locator(".action-bar .btn").nth(1))           # 'Sofort laden"
    assert page.locator(".confirm-title").is_visible()
    assert device_actions(ph, "fast") == []
    ph.tap(page.locator("[data-confirm=yes]"))
    assert len(device_actions(ph, "fast")) == 1


def test_battery_lock_needs_confirmation(phone):
    ph = phone()
    page = ph.open("/batterie")
    ph.tap(page.get_by_role("radio", name="Entladung sperren"))
    ph.tap(page.locator(".manual-params .btn.primary"))
    assert page.locator(".confirm-title").inner_text() == "Entladung sperren?"
    assert not [w for w in ph.actions() if w[1] == "battery/manual"]
    ph.tap(page.locator("[data-confirm=yes]"))
    sent = [w for w in ph.actions() if w[1] == "battery/manual"]
    assert len(sent) == 1 and sent[0][2]["action"] == "no_discharge"


def test_sheet_closes_by_dragging_the_handle_not_the_content(phone):
    ph = phone()
    page = open_target_sheet(ph)
    body = page.locator(".msheet-body")
    ph.swipe_over(body, 0, 220)                 # Inhalt nach unten wischen: bleibt offen
    assert sheet_open(page)
    head = page.locator(".msheet-head")
    ph.swipe_over(head, 0, 260)                 # am Griff ziehen: schließt
    page.wait_for_timeout(400)
    assert not sheet_open(page)
    assert ph.actions() == []


def test_mock_is_offline_from_real_devices(phone):
    """Sicherheitsnetz: Jeder Schreibzugriff landet im Mock, nichts geht ans Netz."""
    ph = phone()
    page = ph.open("/")
    assert json.loads(page.evaluate("JSON.stringify(location.host)")) == "localhost:5173"


# ---------------------------------------------------------------- Ursache (Dokumentation)

def test_root_cause_native_range_changes_while_scrolling(browser):
    """Warum es am Handy keinen freien Regler mehr gibt: Chrome setzt einen
    nativen `<input type=range>` schon beim Berühren der Spur auf die
    Tippstelle – auch wenn der Finger danach senkrecht scrollt. Der alte
    Regler schickte den Wert bei `touchend` ab. Ergebnis: Scrollen über eine
    Karte änderte Ladegrenze oder Zieltemperatur."""
    if browser.browser_type.name != "chromium":
        return
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    page = ctx.new_page()
    page.set_content("""<meta name=viewport content="width=device-width"><div style="height:300px"></div>
      <input id=r type=range min=0 max=100 value=50 style="width:300px;height:44px;margin-left:40px;touch-action:pan-y">
      <div style="height:2000px"></div>
      <script>window.sent=[];document.getElementById('r').addEventListener('touchend',e=>sent.push(e.target.value));</script>""")
    cdp = ctx.new_cdp_session(page)
    box = page.locator("#r").bounding_box()
    x, y = box["x"] + box["width"] * 0.85, box["y"] + 22
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    for i in range(1, 13):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y - i * 15}]})
        page.wait_for_timeout(16)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(300)
    assert page.evaluate("scrollY") > 100            # es wurde gescrollt …
    assert page.evaluate("r.value") != "50"          # … und trotzdem der Wert geändert
    assert page.evaluate("sent") != []               # … und (alter Code) abgeschickt
    ctx.close()
