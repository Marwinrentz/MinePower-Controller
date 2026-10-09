"""End-to-End-Smoke-Test gegen die laufende MinePower-Instanz.

Läuft IM App-Container (alle Abhängigkeiten vorhanden):
    docker compose exec app python scripts/smoke_test.py

Prüft: Health → Setup/Login → Treiberkatalog (inkl. Tesla & Sungrow!) →
Demo-Anlage → Regelkreis (Snapshot, Werte plausibel) → Overrides →
Historie/Statistik/Events/Export → WebSocket-Live-Push.
Wiederholbar: erkennt bestehende Installation und loggt sich ein.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
ADMIN = {"email": "smoke@minepower.de", "password": "smoketest123", "name": "Smoke"}

TOKEN: str | None = None
PASSED: list[str] = []
FAILED: list[str] = []


def call(method: str, path: str, body: dict | None = None, expect: int = 200) -> dict | list | None:
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as resp:
            payload = resp.read()
            if resp.status != expect:
                raise AssertionError(f"{method} {path}: Status {resp.status} (erwartet {expect})")
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        if e.code == expect:
            return json.loads(e.read() or b"{}")
        raise AssertionError(f"{method} {path}: HTTP {e.code} – {e.read().decode()[:200]}")


def check(name: str, fn) -> object | None:
    try:
        result = fn()
        PASSED.append(name)
        print(f"  ✓ {name}")
        return result
    except Exception as exc:  # noqa: BLE001
        FAILED.append(f"{name}: {exc}")
        print(f"  ✗ {name}: {exc}")
        return None


def main() -> int:
    global TOKEN
    print("== MinePower Smoke-Test ==")

    # 1. Health
    check("Health-Endpoint", lambda: call("GET", "/api/system/health"))

    # 2. Setup oder Login (wiederholbar)
    state = call("GET", "/api/system/setup-state")
    assert isinstance(state, dict)
    if state.get("needs_admin"):
        resp = call("POST", "/api/auth/setup", ADMIN)
    else:
        try:
            resp = call("POST", "/api/auth/login", {"email": ADMIN["email"], "password": ADMIN["password"]})
        except AssertionError:
            print("  ! Smoke-Admin existiert nicht – Demo-Login-Versuch")
            resp = call("POST", "/api/auth/login", {"email": "demo@minepower.de", "password": "demo1234"})
    assert isinstance(resp, dict) and resp.get("token"), "kein Token erhalten"
    TOKEN = resp["token"]
    PASSED.append("Auth (Setup/Login)")
    print("  ✓ Auth (Setup/Login)")

    # 3. Treiberkatalog – DER Test für „Tesla/Sungrow nicht auswählbar"
    drivers = call("GET", "/api/drivers")
    assert isinstance(drivers, list)
    ids = sorted(d["id"] for d in drivers)
    print(f"  · {len(ids)} Treiber registriert: {', '.join(ids)}")
    required = [
        "sim_inverter", "sim_meter", "sim_wallbox", "sim_water_heater", "sim_battery",
        "sungrow_sg", "sungrow_sh", "sungrow_battery", "sungrow_meter", "sungrow_wallbox",
        "tesla_vehicle", "goe_charger", "shelly_3em", "generic_modbus_meter",
        "mypv_acthor", "fronius_inverter", "fronius_meter", "sma_inverter", "sma_meter",
        "ocpp_wallbox", "shelly_relay_heater", "sunspec_inverter", "sunspec_meter",
        "huawei_sun2000", "huawei_meter",
    ]
    for rid in required:
        check(f"Treiber verfügbar: {rid}", lambda r=rid: (_ for _ in ()).throw(AssertionError("fehlt")) if r not in ids else True)

    def cat_check():
        wallboxes = call("GET", "/api/drivers?category=wallbox")
        assert isinstance(wallboxes, list)
        wb_ids = [d["id"] for d in wallboxes]
        assert "tesla_vehicle" in wb_ids, f"tesla_vehicle nicht in Wallbox-Kategorie: {wb_ids}"
        inverters = call("GET", "/api/drivers?category=inverter")
        assert isinstance(inverters, list)
        inv_ids = [d["id"] for d in inverters]
        assert "sungrow_sh" in inv_ids and "sungrow_sg" in inv_ids, f"Sungrow fehlt in Inverter-Kategorie: {inv_ids}"
        # Jeder Treiber muss vollständige Feld-Metadaten liefern (Formular-Generierung)
        for d in wallboxes + inverters:
            assert "fields" in d and isinstance(d["fields"], list), f"{d['id']}: fields fehlen"
    check("Kategorie-Filter + Formular-Metadaten (Tesla/Sungrow auswählbar)", cat_check)

    # 4. Demo-Anlage anlegen (idempotent)
    check("Demo-Modus aktivierbar", lambda: call("POST", "/api/system/demo"))
    devices = call("GET", "/api/devices")
    assert isinstance(devices, list)
    check("≥5 Geräte vorhanden", lambda: True if len(devices) >= 5 else (_ for _ in ()).throw(AssertionError(f"nur {len(devices)}")))

    # 5. Verbindungstest-Endpoint
    def conn_test():
        r = call("POST", "/api/devices/test", {"driver_id": "sim_inverter", "config": {"kwp": 5}})
        assert isinstance(r, dict) and r.get("ok"), f"Testverbindung fehlgeschlagen: {r}"
        # Der Test muss aussagekräftige Live-Werte liefern, nicht nur „ok“
        assert r.get("values"), "kein Live-Readback"
        assert isinstance(r.get("warnings"), list), "warnings fehlen im Testergebnis"
    check("Verbindungstest mit Live-Readback", conn_test)

    def write_test():
        """Schreibtest: Der Assistent muss zeigen können, ob ein Gerät Befehle
        wirklich übernimmt – nicht nur, ob es antwortet."""
        r = call("POST", "/api/devices/test",
                 {"driver_id": "sim_water_heater", "config": {"rated_power": 3000},
                  "include_write": True})
        assert isinstance(r, dict) and r.get("ok"), f"Testverbindung fehlgeschlagen: {r}"
        wt = r.get("write_test")
        assert isinstance(wt, dict), "kein Schreibtest-Ergebnis"
        assert wt.get("ok") is True, f"Testbefehl nicht übernommen: {wt}"
    check("Verbindungstest mit Steuerbefehl + Readback", write_test)

    # 6. Regelkreis: Snapshot nach ein paar Ticks
    time.sleep(8)
    snap = call("GET", "/api/status/live")
    assert isinstance(snap, dict)

    def snapshot_check():
        assert snap.get("time"), "kein Zeitstempel"
        assert isinstance(snap.get("pv_power"), (int, float)), "pv_power fehlt"
        assert snap.get("grid_power") is not None, "grid_power fehlt (Zähler offline?)"
        assert snap.get("battery"), "Batterie fehlt im Snapshot"
        for key in ("waste_w", "gap_filled_w", "deadband_w"):
            assert key in snap, f"Kennzahl '{key}' fehlt im Snapshot"
        devs = snap.get("devices") or []
        online = [d for d in devs if d.get("online")]
        assert len(online) >= 5, f"nur {len(online)} Geräte online: " + str(
            [(d['name'], d.get('last_error')) for d in devs if not d.get('online')])
    check("Live-Snapshot plausibel (alle Sim-Geräte online)", snapshot_check)

    # 7. Manueller Override: Sofortladen → Regelung reagiert → zurück auf Auto
    wallbox = next((d for d in (snap.get("devices") or []) if d.get("category") == "wallbox"), None)

    def override_check():
        assert wallbox, "keine Wallbox im Snapshot"
        call("POST", f"/api/devices/{wallbox['id']}/action", {"action": "fast"})
        time.sleep(7)
        snap2 = call("GET", "/api/status/live")
        assert isinstance(snap2, dict)
        wb2 = next(d for d in snap2["devices"] if d["id"] == wallbox["id"])
        assert wb2.get("override") == "fast", f"Override nicht gesetzt: {wb2.get('override')}"
        decision = wb2.get("decision") or {}
        assert decision.get("enable") is True, f"Volllast nicht aktiviert: {decision}"
        call("POST", f"/api/devices/{wallbox['id']}/action", {"action": "auto"})
    check("Override Sofortladen → Volllast → Automatik", override_check)

    # 8. Historie, Statistik, Events, Export
    check("Historie liefert Zeitreihen", lambda: call("GET", "/api/history?fields=pv_power&range=1h"))
    def stats_check():
        r = call("GET", "/api/statistics?range=24h")
        assert isinstance(r, dict)
        for key in ("autarky_pct", "wasted_kwh", "wasted_pct", "wasted_value_eur"):
            assert key in r, f"Kennzahl '{key}' fehlt in der Statistik"
    check("Statistik berechnet Kennzahlen inkl. verschenkter Energie", stats_check)
    check("Ereignis-Log gefüllt", lambda: (_ for _ in ()).throw(AssertionError("leer"))
          if not call("GET", "/api/events") else True)
    def diag_check():
        r = call("GET", "/api/system/diagnostics")
        assert isinstance(r, dict)
        for key in ("deadband_effective_w", "volatility_index", "waste_w"):
            assert key in r, f"Diagnosewert '{key}' fehlt"
    check("Diagnose-Endpoint inkl. Regelgüte", diag_check)

    def export_check():
        data = call("GET", "/api/system/export")
        assert isinstance(data, dict) and data.get("devices"), "Export leer"
    check("Config-Export", export_check)

    # 9. WebSocket-Live-Push
    async def ws_check():
        import websockets

        async with websockets.connect(f"ws://localhost:8000/api/ws?token={TOKEN}") as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            assert msg.get("type") == "snapshot" and msg["data"].get("devices"), "kein Snapshot über WS"
    check("WebSocket-Snapshot", lambda: asyncio.run(ws_check()))

    # Ergebnis
    print(f"\n== Ergebnis: {len(PASSED)} bestanden, {len(FAILED)} fehlgeschlagen ==")
    for f in FAILED:
        print(f"  FEHLER: {f}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
