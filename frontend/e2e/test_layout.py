"""Safe-Areas am Handy: Dynamic Island, Home-Indikator, Querformat.

Für jedes Geräteprofil (hoch und quer) wird geprüft, dass nichts Bedienbares
oder Lesbares unter Insel, Home-Indikator oder seitlichen Einzügen liegt und
nichts seitlich überläuft.
"""
from __future__ import annotations

import pytest

from shots import PHONES

PROFILES = list(PHONES)

RECTS_JS = """(sel) => [...document.querySelectorAll(sel)].filter(e => {
  const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
  return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity > 0.05;
}).map(e => { const r = e.getBoundingClientRect(); return {x: r.left, y: r.top, r: r.right, b: r.bottom, t: (e.innerText || e.getAttribute('aria-label') || e.className).toString().slice(0, 40)}; })"""


def island_rect(w: int, h: int):
    if w > h:
        return {"x": 11, "y": h / 2 - 63, "r": 48, "b": h / 2 + 63}
    return {"x": w / 2 - 63, "y": 11, "r": w / 2 + 63, "b": 48}


def overlaps(a, b) -> bool:
    return a["x"] < b["r"] and a["r"] > b["x"] and a["y"] < b["b"] and a["b"] > b["y"]


@pytest.mark.parametrize("profile", PROFILES)
def test_shell_respects_safe_areas(phone, profile):
    ph = phone(profile=profile)
    page = ph.open("/")
    w, h = ph.size
    safe = ph.safe
    interactive = page.evaluate(RECTS_JS, "button, a, [role=radio], .brand, .live, .hero-title, .tab span")
    # Nichts Bedienbares unter der Insel
    if ph.island:
        isl = island_rect(w, h)
        hits = [e for e in interactive if overlaps(e, isl)]
        assert not hits, hits[:3]
    # Kopfzeile unterhalb der Statusleiste
    for e in page.evaluate(RECTS_JS, ".topbar .brand, .topbar .live"):
        assert e["y"] >= safe["top"] - 0.5, e
    # Tab-Leiste über dem Home-Indikator, innerhalb der seitlichen Einzüge
    for e in page.evaluate(RECTS_JS, ".tabbar .tab"):
        assert e["b"] <= h - safe["bottom"] + 0.5, e
        assert e["x"] >= safe["left"] - 0.5 and e["r"] <= w - safe["right"] + 0.5, e
    # Querformat: Leiste links rechts neben dem Einzug, Inhalt vor dem rechten Einzug
    for e in page.evaluate(RECTS_JS, ".sidebar .side-item"):
        assert e["x"] >= safe["left"] - 0.5, e
    for e in page.evaluate(RECTS_JS, ".main > .page > *, .action-bar"):
        assert e["r"] <= w - safe["right"] + 0.5, e
        assert e["x"] >= safe["left"] - 0.5, e
    # Sprunglink bleibt versteckt (lugte früher unter der Insel hervor)
    skip = page.evaluate(RECTS_JS, ".skip-link")
    assert all(e["b"] <= 0 for e in skip), skip
    # Kein seitliches Scrollen
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0


@pytest.mark.parametrize("profile", ["iphone15", "iphone15-quer", "android360"])
def test_sheet_actions_sit_above_home_indicator(phone, profile):
    ph = phone(profile=profile)
    page = ph.open("/warmwasser")
    ph.tap(page.locator(".tval-bar").first)
    page.wait_for_timeout(700)
    w, h = ph.size
    foot = page.evaluate(RECTS_JS, ".msheet-foot button")
    assert foot, "Blatt ohne Fußleiste"
    for e in foot:
        assert e["b"] <= h - ph.safe["bottom"] + 0.5, e
        assert e["x"] >= ph.safe["left"] - 0.5 and e["r"] <= w - ph.safe["right"] + 0.5, e
    head = page.evaluate(RECTS_JS, ".msheet-title, .msheet-close")
    for e in head:
        assert e["y"] >= ph.safe["top"] - 0.5, e


@pytest.mark.parametrize("path", ["/einstellungen?tab=devices", "/einstellungen?tab=regulation", "/auto"])
def test_inputs_do_not_trigger_zoom(phone, path):
    """iOS zoomt in Felder unter 16 px hinein – das wirkt wie ein Fehler."""
    ph = phone()
    page = ph.open(path)
    sizes = page.evaluate("""[...document.querySelectorAll('input:not([type=range]):not([type=checkbox]), select, textarea')]
      .map(e => parseFloat(getComputedStyle(e).fontSize))""")
    assert all(s >= 16 for s in sizes), sizes


@pytest.mark.parametrize("path", ["/", "/auto", "/warmwasser", "/batterie", "/tarif", "/statistik", "/diagnose", "/einstellungen"])
def test_no_horizontal_overflow(phone, path):
    ph = phone(profile="android360")
    page = ph.open(path)
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
