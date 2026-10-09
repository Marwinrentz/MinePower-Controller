"""Screenshots mit Mock-Daten in festen Geräteprofilen.

    python e2e/shots.py desktop  <ausgabe>            # Desktop-Seiten (Vergleich vorher/nachher)
    python e2e/shots.py mobile   <ausgabe> [webkit]   # Handy-Profile, hoch und quer
    python e2e/shots.py wall     <ausgabe>            # Wand-Ansicht 1080p und 4K, alle Zustände

Erwartet den Vite-Dev-Server auf :5173 (kein Backend nötig – alles gemockt).
Safe-Areas werden über die CSS-Variablen `--safe-*` simuliert und als
Overlay (Dynamic Island, Home-Indikator) mit ins Bild gezeichnet.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from mock import Mock  # noqa: E402

UI = "http://localhost:5173"

#: name: (breite, höhe, dpr, safe oben, rechts, unten, links, insel)
PHONES = {
    "iphone15": (393, 852, 2, 59, 0, 34, 0, True),
    "iphone15-quer": (852, 393, 2, 0, 59, 21, 59, True),
    "iphonese": (375, 667, 2, 20, 0, 0, 0, False),
    "iphonese-quer": (667, 375, 2, 0, 0, 0, 0, False),
    "android360": (360, 800, 2, 24, 0, 16, 0, False),
    "android360-quer": (800, 360, 2, 0, 0, 16, 24, False),
    "android412": (412, 915, 2, 28, 0, 16, 0, False),
    "android412-quer": (915, 412, 2, 0, 0, 16, 28, False),
}

DESKTOP_PAGES = ["/", "/auto", "/warmwasser", "/batterie", "/tarif", "/statistik", "/diagnose",
                 "/einstellungen", "/einstellungen?tab=battery", "/einstellungen?tab=regulation"]


def safe_area_script(top: int, right: int, bottom: int, left: int, island: bool, landscape: bool) -> str:
    """Safe-Area-Variablen setzen und Insel/Home-Indikator einzeichnen."""
    overlay = ""
    if island:
        if landscape:
            overlay += ("<div style='position:fixed;left:11px;top:50%;width:37px;height:126px;margin-top:-63px;"
                        "background:#000;border-radius:20px;z-index:2147483647;pointer-events:none'></div>")
        else:
            overlay += ("<div style='position:fixed;top:11px;left:50%;width:126px;height:37px;margin-left:-63px;"
                        "background:#000;border-radius:20px;z-index:2147483647;pointer-events:none'></div>")
    if bottom:
        overlay += ("<div style='position:fixed;bottom:8px;left:50%;width:134px;height:5px;margin-left:-67px;"
                    "background:rgba(0,0,0,.55);border-radius:3px;z-index:2147483647;pointer-events:none;"
                    "box-shadow:0 0 0 1px rgba(255,255,255,.35)'></div>")
    return f"""
    (() => {{
      const css = `:root {{ --safe-top: {top}px !important; --safe-right: {right}px !important;
                           --safe-bottom: {bottom}px !important; --safe-left: {left}px !important; }}`;
      const add = () => {{
        const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
        const ov = document.createElement('div'); ov.id = '__safe_overlay'; ov.innerHTML = `{overlay}`;
        document.body.appendChild(ov);
      }};
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
    }})();
    """


def phone_context(browser, profile: str, theme: str, reduced: bool = True):
    w, h, dpr, top, right, bottom, left, island = PHONES[profile]
    is_webkit = browser.browser_type.name == "webkit"
    ctx = browser.new_context(
        viewport={"width": w, "height": h}, device_scale_factor=dpr, is_mobile=not is_webkit, has_touch=True,
        color_scheme=theme, locale="de-DE", timezone_id="Europe/Berlin",
        reduced_motion="reduce" if reduced else "no-preference",
    )
    ctx.add_init_script(safe_area_script(top, right, bottom, left, island, w > h))
    return ctx


def launch(p, engine: str):
    if engine == "webkit":
        return p.webkit.launch()
    return p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)


def shoot(page, path: Path, full: bool = False):
    page.wait_for_timeout(1200)
    page.screenshot(path=str(path), full_page=full)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    print(f"{path.name}: overflow={overflow}")


def desktop(out: Path):
    with sync_playwright() as p:
        browser = launch(p, "chromium")
        for theme in ("light", "dark"):
            for w, h in ((1440, 900), (1920, 1080)):
                ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=theme, locale="de-DE",
                                          timezone_id="Europe/Berlin", reduced_motion="reduce")
                page = ctx.new_page()
                Mock(page, "sun", theme=theme)
                for route in DESKTOP_PAGES:
                    page.goto(UI + route, wait_until="networkidle")
                    page.wait_for_timeout(1500)
                    name = route.strip("/").replace("?tab=", "-").replace("/", "-") or "home"
                    shoot(page, out / f"desktop-{w}-{theme}-{name}.png", full=True)
                ctx.close()
        browser.close()


def mobile(out: Path, engine: str = "chromium"):
    pages = [("/", "home"), ("/auto", "auto"), ("/warmwasser", "wasser"), ("/batterie", "batterie"),
             ("/einstellungen?tab=battery", "einst-batterie")]
    with sync_playwright() as p:
        browser = launch(p, engine)
        for profile in PHONES:
            for theme in ("light", "dark"):
                if theme == "dark" and profile not in ("iphone15", "android412", "iphone15-quer"):
                    continue
                ctx = phone_context(browser, profile, theme)
                page = ctx.new_page()
                Mock(page, "sun", theme=theme)
                for route, name in pages if profile in ("iphone15", "android360") else pages[:1]:
                    page.goto(UI + route, wait_until="networkidle")
                    shoot(page, out / f"{engine}-{profile}-{theme}-{name}.png")
                ctx.close()
        browser.close()


def states(out: Path, engine: str = "chromium"):
    """Handy-Startseite in allen Zuständen."""
    from mock import SCENARIOS
    with sync_playwright() as p:
        browser = launch(p, engine)
        for name in SCENARIOS:
            theme = "dark" if name in ("night", "offline", "boost") else "light"
            ctx = phone_context(browser, "iphone15", theme)
            page = ctx.new_page()
            Mock(page, name, theme=theme)
            page.goto(UI + "/", wait_until="networkidle")
            shoot(page, out / f"{engine}-state-{name}-{theme}.png")
            ctx.close()
        browser.close()


def sheets(out: Path, engine: str = "chromium"):
    """Bedien-Blätter am Handy: Wert einstellen und Bestätigung."""
    with sync_playwright() as p:
        browser = launch(p, engine)
        for theme in ("light", "dark"):
            ctx = phone_context(browser, "iphone15", theme)
            page = ctx.new_page()
            Mock(page, "sun", theme=theme)
            page.goto(UI + "/warmwasser", wait_until="networkidle")
            page.locator(".tval-bar").first.evaluate("el => el.scrollIntoView({block: 'center'})")
            shoot(page, out / f"{engine}-wasser-regler-{theme}.png")
            page.locator(".tval-bar").first.click()
            page.wait_for_timeout(900)
            shoot(page, out / f"{engine}-sheet-wert-{theme}.png")
            page.keyboard.press("Escape")
            page.goto(UI + "/", wait_until="networkidle")
            page.locator(".action-bar .btn").first.click()
            page.wait_for_timeout(900)
            shoot(page, out / f"{engine}-sheet-confirm-{theme}.png")
            ctx.close()
        browser.close()


def wall(out: Path, scenarios: list[str] | None = None):
    from mock import SCENARIOS
    with sync_playwright() as p:
        browser = launch(p, "chromium")
        for w, h in ((1920, 1080), (3840, 2160)):
            for theme in ("dark", "light"):
                for name in scenarios or SCENARIOS:
                    if w == 3840 and name not in ("sun", "night", "error"):
                        continue
                    ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=theme,
                                              locale="de-DE", timezone_id="Europe/Berlin")
                    page = ctx.new_page()
                    Mock(page, name, theme=theme)
                    page.goto(UI + "/wall", wait_until="networkidle")
                    page.wait_for_timeout(1800)
                    shoot(page, out / f"wall-{w}-{theme}-{name}.png")
                    ctx.close()
        browser.close()


if __name__ == "__main__":
    kind, target = sys.argv[1], Path(sys.argv[2])
    target.mkdir(parents=True, exist_ok=True)
    if kind == "desktop":
        desktop(target)
    elif kind == "mobile":
        mobile(target, sys.argv[3] if len(sys.argv) > 3 else "chromium")
    elif kind == "states":
        states(target, sys.argv[3] if len(sys.argv) > 3 else "chromium")
    elif kind == "sheets":
        sheets(target, sys.argv[3] if len(sys.argv) > 3 else "chromium")
    elif kind == "wall":
        wall(target, sys.argv[3].split(",") if len(sys.argv) > 3 else None)
