"""Gemeinsame Fixtures der Touch-Tests (Playwright, Chromium + WebKit).

Voraussetzung: Vite-Dev-Server auf :5173 (`npm run dev`). Das Backend wird
vollständig gemockt (mock.py), echte Geräte werden nie angesprochen.

    pip install playwright pytest && python -m playwright install webkit
    pytest e2e -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from mock import Mock  # noqa: E402
from shots import PHONES, safe_area_script  # noqa: E402

UI = os.environ.get("MINEPOWER_UI", "http://localhost:5173")
CHROME = os.environ.get("CHROME_PATH", "/usr/bin/google-chrome")
ENGINES = os.environ.get("E2E_ENGINES", "chromium,webkit").split(",")


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session", params=ENGINES)
def browser(pw, request):
    if request.param == "webkit":
        b = pw.webkit.launch()
    else:
        b = pw.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None)
    yield b
    b.close()


class Phone:
    """Ein Handy-Profil mit Mock-Backend und Touch-Gesten."""

    def __init__(self, browser, profile: str = "iphone15", scenario: str = "sun", theme: str = "light",
                 reduced: bool = True):
        w, h, dpr, top, right, bottom, left, island = PHONES[profile]
        self.engine = browser.browser_type.name
        self.size = (w, h)
        self.safe = {"top": top, "right": right, "bottom": bottom, "left": left}
        self.island = island
        self.ctx = browser.new_context(
            viewport={"width": w, "height": h}, device_scale_factor=dpr, has_touch=True,
            is_mobile=self.engine == "chromium", color_scheme=theme, locale="de-DE", timezone_id="Europe/Berlin",
            reduced_motion="reduce" if reduced else "no-preference",
        )
        self.ctx.add_init_script(safe_area_script(top, right, bottom, left, island, w > h))
        self.page = self.ctx.new_page()
        self.mock = Mock(self.page, scenario, theme=theme)
        self._cdp = self.ctx.new_cdp_session(self.page) if self.engine == "chromium" else None

    def open(self, path: str = "/"):
        self.page.goto(UI + path, wait_until="networkidle")
        self.page.wait_for_timeout(600)
        return self.page

    def close(self):
        self.ctx.close()

    # ------------------------------------------------------------ Gesten
    def tap(self, locator):
        # Mittig einblenden: am Rand läge das Ziel unter der festen Tab-Leiste
        locator.evaluate("el => el.scrollIntoView({block: 'center', inline: 'center'})")
        self.page.wait_for_timeout(200)
        box = locator.bounding_box()
        self.page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        self.page.wait_for_timeout(250)

    def swipe(self, x0: float, y0: float, x1: float, y1: float, steps: int = 14, target=None):
        """Wischgeste. Chromium: echte Touch-Events über CDP (inkl. Scrollen
        und nativer Gestenerkennung). WebKit: Pointer-Events auf dem Element
        unter dem Startpunkt (wie Safari sie an die Seite liefert)."""
        if self._cdp:
            send = self._cdp.send
            send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0, "id": 1}]})
            for i in range(1, steps + 1):
                x = x0 + (x1 - x0) * i / steps
                y = y0 + (y1 - y0) * i / steps
                send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y, "id": 1}]})
                self.page.wait_for_timeout(16)
            send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        else:
            self.page.evaluate(
                """([x0, y0, x1, y1, steps]) => {
                  const el = document.elementFromPoint(x0, y0);
                  const fire = (type, x, y) => el.dispatchEvent(new PointerEvent(type, {
                    bubbles: true, cancelable: true, composed: true, pointerId: 7, pointerType: 'touch',
                    isPrimary: true, clientX: x, clientY: y, button: 0, buttons: type === 'pointerup' ? 0 : 1 }));
                  fire('pointerdown', x0, y0);
                  for (let i = 1; i <= steps; i++) fire('pointermove', x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps);
                  // Senkrecht = Safari übernimmt zum Scrollen und bricht den Zeiger ab
                  const vertical = Math.abs(y1 - y0) > Math.abs(x1 - x0);
                  fire(vertical ? 'pointercancel' : 'pointerup', x1, y1);
                }""",
                [x0, y0, x1, y1, steps],
            )
        self.page.wait_for_timeout(350)

    def swipe_over(self, locator, dx: float, dy: float):
        # Mittig einblenden: am Rand läge das Ziel unter der festen Tab-Leiste
        locator.evaluate("el => el.scrollIntoView({block: 'center', inline: 'center'})")
        self.page.wait_for_timeout(200)
        box = locator.bounding_box()
        x0, y0 = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.swipe(x0, y0, x0 + dx, y0 + dy)

    def actions(self):
        return [w for w in self.mock.writes if not w[1].startswith("auth/")]


@pytest.fixture
def phone(browser):
    made: list[Phone] = []

    def make(**kw) -> Phone:
        ph = Phone(browser, **kw)
        made.append(ph)
        return ph

    yield make
    for ph in made:
        ph.close()
