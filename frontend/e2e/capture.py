"""Mock-Daten einmalig aus der lokalen Demo-Instanz mitschneiden.

Startet nichts selbst: erwartet das Demo-Backend (DEMO_MODE, :8000) und den
Vite-Dev-Server (:5173). Besucht alle Seiten, speichert jede GET-Antwort
unter /api und einen Live-Snapshot nach `fixtures/api.json` bzw.
`fixtures/snapshot.json`. Anmeldedaten werden nicht gespeichert.

    python e2e/capture.py
"""
import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

API, UI = "http://localhost:8000", "http://localhost:5173"
OUT = Path(__file__).parent / "fixtures"
PAGES = ["/", "/auto", "/warmwasser", "/batterie", "/tarif", "/statistik", "/diagnose",
         "/diagnose?tab=devices", "/diagnose?tab=events", "/einstellungen", "/einstellungen?tab=battery",
         "/einstellungen?tab=devices", "/einstellungen?tab=regulation", "/einstellungen?tab=users", "/wall"]


def login():
    req = urllib.request.Request(API + "/api/auth/login", method="POST",
                                 data=json.dumps({"email": "demo@minepower.de", "password": "demo1234"}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main():
    auth = login()
    store: dict[str, object] = {}
    snapshot: dict | None = None
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="de-DE", timezone_id="Europe/Berlin")
        ctx.add_init_script(f"localStorage.setItem('mp_token', {json.dumps(auth['token'])});"
                            f"localStorage.setItem('mp_user', {json.dumps(json.dumps(auth['user']))});")
        page = ctx.new_page()

        def on_response(resp):
            url = resp.url
            if "/api/" not in url or resp.request.method != "GET" or "/api/ws" in url:
                return
            key = url.split("/api/", 1)[1]
            if key.startswith("auth/refresh"):
                return
            try:
                store[key] = resp.json()
            except Exception:  # noqa: BLE001 – keine JSON-Antwort
                pass

        def on_ws(ws):
            def frame(payload):
                nonlocal snapshot
                try:
                    msg = json.loads(payload)
                except Exception:  # noqa: BLE001
                    return
                if msg.get("type") == "snapshot":
                    snapshot = msg["data"]
            ws.on("framereceived", frame)

        page.on("response", on_response)
        page.on("websocket", on_ws)
        for path in PAGES:
            page.goto(UI + path, wait_until="networkidle")
            page.wait_for_timeout(1500)
        browser.close()

    me = dict(auth["user"])
    store["auth/me"] = me
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "api.json").write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(store), "Antworten,", "Snapshot" if snapshot else "KEIN Snapshot")
    print("\n".join(sorted(store)))


if __name__ == "__main__":
    main()
