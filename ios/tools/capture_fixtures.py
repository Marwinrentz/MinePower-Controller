"""Echte API-Antworten eines Demo-Backends als Test-Fixtures mitschneiden.

    ios/tools/demo-server.sh 18080 &   # oder ein laufendes Demo-Backend
    python3 ios/tools/capture_fixtures.py http://127.0.0.1:18080

Schreibt nach ios/MinePowerKit/Tests/MinePowerKitTests/Fixtures/. Nur Demo-
Daten, keine echten Anlagen."""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18080"
OUT = Path(__file__).resolve().parents[1] / "MinePowerKit/Tests/MinePowerKitTests/Fixtures"


def call(path: str, token: str | None = None, body=None, method: str | None = None, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode()
    return text if raw else (json.loads(text) if text else None)


def save(name: str, value) -> None:
    path = OUT / name
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    print("  ", name)


async def ws_snapshot(token: str) -> dict:
    import websockets
    url = BASE.replace("http", "ws", 1) + f"/api/ws?token={token}"
    async with websockets.connect(url) as ws:
        for _ in range(10):
            msg = json.loads(await ws.recv())
            if msg.get("type") == "snapshot":
                return msg
    raise RuntimeError("kein Snapshot")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for _ in range(60):
        try:
            save("health.json", call("/api/system/health"))
            break
        except Exception:
            time.sleep(0.5)
    save("setup-state.json", call("/api/system/setup-state"))
    login = call("/api/auth/login", body={"email": "demo@minepower.de", "password": "demo1234"})
    token = login["token"]
    save("login.json", {**login, "token": "<token>"})
    time.sleep(8)  # einige Regeltakte für Verlauf und Entscheidungen
    gets = {
        "me.json": "/api/auth/me", "users.json": "/api/auth/users", "devices.json": "/api/devices",
        "limits.json": "/api/devices/limits", "drivers.json": "/api/drivers", "settings.json": "/api/settings",
        "schedules.json": "/api/settings/schedules", "live.json": "/api/status/live",
        "battery-status.json": "/api/battery/status", "history-24h.json": "/api/history?range=24h&fields=pv_power,grid_power,house_power,battery_power,battery_soc,wallbox_power,water_power,price_ct",
        "statistics-24h.json": "/api/statistics?range=24h", "statistics-30d.json": "/api/statistics?range=30d",
        "tariff.json": "/api/statistics/tariff", "forecast.json": "/api/statistics/forecast",
        "sessions.json": "/api/sessions?limit=40", "events.json": "/api/events?limit=50",
        "findings.json": "/api/system/findings?range=7d", "diagnostics.json": "/api/system/diagnostics",
        "changelog.json": "/api/system/changelog", "backup-status.json": "/api/system/backup-status",
        "vehicles.json": "/api/vehicles", "mcp-tokens.json": "/api/mcp/tokens", "mcp-tools.json": "/api/mcp/tools",
    }
    for name, path in gets.items():
        try:
            save(name, call(path, token))
        except Exception as exc:  # noqa: BLE001
            print("!!", name, exc)
    save("sessions-export.csv", call("/api/statistics/export.csv?range=30d", token, raw=True))
    save("ws-snapshot.json", asyncio.run(ws_snapshot(token)))


if __name__ == "__main__":
    main()
