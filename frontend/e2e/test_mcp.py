"""Einstellungen → KI-Assistent: Schlüssel erzeugen, Anleitung je Anwendung."""
from __future__ import annotations


def test_create_key_and_switch_guide(phone):
    ph = phone()
    page = ph.open("/einstellungen?tab=mcp")
    page.locator(".token-list li").first.wait_for(state="visible")
    assert "Claude Desktop" in page.locator(".token-list").inner_text()
    page.fill(".form-grid input.input", "Cursor")
    ph.tap(page.get_by_role("button", name="Schlüssel erzeugen"))
    page.wait_for_timeout(400)
    created = [w for w in ph.mock.writes if w[0] == "POST" and w[1] == "mcp/tokens"]
    assert created and created[0][2] == {"name": "Cursor", "role": "readonly"}
    assert "mp_Ab3x" in page.locator(".callout").inner_text()
    ph.tap(page.get_by_role("tab", name="Cursor"))
    code = page.locator(".client-steps + .copy-block pre").inner_text()
    assert '"Authorization": "Bearer mp_Ab3x' in code and "/mcp" in code
    ph.tap(page.get_by_role("tab", name="Claude Code"))
    assert "claude mcp add --transport http minepower" in page.locator(".client-steps + .copy-block pre").inner_text()


def test_url_only_connector_shows_key_address(phone):
    ph = phone()
    page = ph.open("/einstellungen?tab=mcp")
    page.fill(".form-grid input.input", "Grok")
    ph.tap(page.get_by_role("button", name="Schlüssel erzeugen"))
    page.wait_for_timeout(300)
    ph.tap(page.get_by_role("tab", name="Grok"))
    page.fill("input[type=url]", "https://mp.example.com/")
    code = page.locator(".client-steps + .copy-block pre").inner_text()
    assert code == "https://mp.example.com/mcp/k/mp_Ab3xBEISPIELNURFUERTESTS0000000000000000"
    assert page.locator(".callout.warn").count() == 1
