"""Schnelltest: Seite laden, Konsolenfehler und sichtbaren Text ausgeben."""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from mock import Mock  # noqa: E402

UI = os.environ.get("MINEPOWER_UI", "http://localhost:5173")
path = sys.argv[1] if len(sys.argv) > 1 else "/"
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROME_PATH", "/usr/bin/chromium"))
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    Mock(page, "sun", theme="light")
    errs = []
    page.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    page.goto(UI + path)
    page.wait_for_timeout(3000)
    print("URL", page.url)
    print("H", page.evaluate("document.documentElement.scrollHeight"))
    print("TEXT", page.inner_text("body")[:600].replace("\n", " | "))
    for e in errs[:15]:
        print("ERR", e[:300])
    b.close()
