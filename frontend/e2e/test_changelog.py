"""Changelog-Seite und Hinweisfenster nach einem Update."""
from __future__ import annotations

from pathlib import Path

VERSION = (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()


def seen_calls(ph) -> list:
    return [w for w in ph.mock.writes if w[0] == "POST" and w[1] == "auth/me/seen"]


def test_popup_shows_unseen_versions_and_marks_seen(phone):
    ph = phone()
    ph.mock.unseen = True
    page = ph.open("/")
    sheet = page.locator(".update-sheet")
    sheet.wait_for(state="visible")
    assert VERSION in sheet.inner_text()
    assert any(g in sheet.inner_text() for g in ("Neu", "Geändert", "Behoben"))
    ph.tap(sheet.locator(".msheet-foot .mbtn.primary"))
    page.wait_for_timeout(500)
    assert page.locator(".update-sheet").count() == 0
    assert len(seen_calls(ph)) == 1


def test_no_popup_without_unseen_versions(phone):
    ph = phone()
    page = ph.open("/")
    page.wait_for_timeout(400)
    assert page.locator(".update-sheet").count() == 0
    assert seen_calls(ph) == []


def test_changelog_page_lists_versions(phone):
    ph = phone()
    page = ph.open("/changelog")
    page.locator(".changelog-version").first.wait_for(state="visible")
    text = page.locator(".changelog").inner_text()
    assert VERSION in text
    for group in ("Neu", "Geändert", "Behoben"):
        assert group in text
