"""Mock-Backend für Playwright: feste Antworten statt echter Geräte.

* REST: GET-Antworten aus `fixtures/api.json` (mit `capture.py` aus der
  Demo-Instanz mitgeschnitten), Verlauf und Statistik synthetisch.
  Schreibende Aufrufe (POST/PUT/PATCH/DELETE) werden nur protokolliert –
  daran prüfen die Touch-Tests, ob eine Geste etwas ausgelöst hat.
* Live-Daten: Der WebSocket `/api/ws` wird abgefangen und liefert einen
  Snapshot aus `scenario(name)`.
* Uhrzeit fest (12:30 bzw. 23:30 bei 'night"), damit Screenshots
  vergleichbar sind.

Nutzung:

    from mock import Mock
    mock = Mock(page, "car_charging")   # vor page.goto()
    ...
    mock.writes                         # [(method, path, body), …]
"""
from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).parent / "fixtures"
API = json.loads((FIXTURES / "api.json").read_text(encoding="utf-8"))
BASE = json.loads((FIXTURES / "snapshot.json").read_text(encoding="utf-8"))
USER = API["auth/me"]
LIMITS = {"meter": 1, "battery": 1}
CHANGELOG_FILE = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


def changelog() -> list[dict]:
    """CHANGELOG.md wie im Backend (core/changelog.py) zerlegen."""
    out: list[dict] = []
    group = {"Neu": "new", "Geändert": "changed", "Behoben": "fixed"}
    cur, sec = None, None
    for line in CHANGELOG_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## (\S+) \((\d{4}-\d{2}-\d{2})\)", line)
        if m:
            cur = {"version": m.group(1), "date": m.group(2), "new": [], "changed": [], "fixed": []}
            out.append(cur)
        elif cur and line.startswith("### "):
            sec = group.get(line[4:].strip())
        elif cur and sec and line.startswith("- "):
            cur[sec].append(line[2:].strip())
    return out

DAY = "2026-10-08"
NOON = f"{DAY}T12:30:00+02:00"
NIGHT = f"{DAY}T23:30:00+02:00"

SCENARIOS = [
    "sun", "car_charging", "car_away", "car_asleep", "own_program", "boost", "battery_empty", "battery_full",
    "offline", "error", "night", "grid_import", "waste",
]


def _dev(snap: dict, category: str) -> dict:
    return next(d for d in snap["devices"] if d["category"] == category)


def scenario(name: str = "sun") -> dict:
    """Snapshot für einen Zustand. Grundlage ist ein echter Demo-Snapshot."""
    s = copy.deepcopy(BASE)
    now = datetime.fromisoformat(NIGHT if name == "night" else NOON).astimezone(timezone.utc)
    s["time"] = now.isoformat()
    for d in s["devices"]:
        d["last_seen"] = s["time"]
    pv, house = 6200.0, 620.0
    car, water, bat_w, soc = 0.0, 0.0, 1800.0, 64.0
    s.update(price_ct=24.8, price_cheap=False, price_limit_ct=20.0, waste_w=0.0, waste_reason=None,
             warnings=[], safety=None, paused=False, battery_manual=None, interval_s=10.0)
    wb, wh, bat = _dev(s, "wallbox"), _dev(s, "water_heater"), _dev(s, "battery")
    wb.update(name="Tesla Model Y", mode="pv_only", override=None, override_state=None, presence="plugged", phases=3)
    wb["data"].update(state="connected", power=0.0, soc=58.0, current_set=6.0, phases_active=3)
    wb["decision"] = {"enable": False, "current_a": 0, "reason": "warte auf Überschuss (1209 W)"}
    wh.update(name="Warmwasser", mode="pv_only", boost=False, boost_state=None, self_mode_since_s=None)
    wh["data"].update(power=0.0, temperature_c=52.0, device_mode=None, is_on=False)
    wh["decision"] = {"power_w": 0, "reason": "Zieltemperatur 60 °C erreicht"}
    bat.update(name="Hausbatterie", control_enabled=True, guard_mode="auto")
    s["battery_plan"] = {"intent": "auto", "label": "Automatik", "reason": "Automatik", "source": "auto"}

    if name in ("sun", "car_charging"):
        car = 3450.0
        wb["data"].update(state="charging", power=car, current_set=5.0)
        wb.update(presence="charging", mode="pv_only")
        wb["decision"] = {"enable": True, "current_a": 5, "reason": "PV-Überschuss 3450 W"}
        water = 400.0 if name == "sun" else 0.0
        if water:
            wh["data"].update(power=water, temperature_c=55.0, is_on=True)
            wh["decision"] = {"power_w": water, "reason": "PV-Überschuss 400 W"}
    elif name == "car_away":
        wb.update(presence="away")
        wb["data"].update(state="idle", power=0.0, soc=None, vehicle_reachable=False)
        wb["decision"] = {"enable": False, "current_a": 0, "reason": "kein Fahrzeug"}
        bat_w, soc = 2200.0, 81.0
    elif name == "car_asleep":
        wb.update(presence="asleep_plugged")
        wb["data"].update(state="connected", vehicle_reachable=False)
    elif name == "own_program":
        pv, water, bat_w, soc = 900.0, 2980.0, 0.0, 47.0
        wh.update(self_mode_since_s=420)
        wh["data"].update(power=water, temperature_c=44.0, device_mode="Warmwasser-Sicherstellung", is_on=True)
        wh["decision"] = {"power_w": 0, "reason": "Gerät heizt im eigenen Programm"}
    elif name == "boost":
        pv, water, bat_w, soc = 1800.0, 3000.0, 0.0, 52.0
        wh.update(boost=True, boost_state={"started": s["time"], "until": (now + timedelta(minutes=42)).isoformat(),
                                          "remaining_s": 2520, "end_mode": "both", "target_temp_c": 65.0})
        wh["data"].update(power=water, temperature_c=48.0, is_on=True)
        wh["decision"] = {"power_w": water, "reason": "Boost aktiv"}
    elif name == "battery_empty":
        pv, bat_w, soc = 300.0, 0.0, 20.0
        s["battery_plan"] = {"intent": "no_discharge", "label": "Entladung gesperrt",
                             "reason": "Reserve 20 % erreicht", "source": "reserve"}
    elif name == "battery_full":
        pv, bat_w, soc = 7400.0, 0.0, 100.0
        car = 3450.0
        wb["data"].update(state="charging", power=car, current_set=5.0)
        wb.update(presence="charging")
        water = 2600.0
        wh["data"].update(power=water, temperature_c=58.0, is_on=True)
    elif name == "offline":
        wb.update(online=False, presence="proxy_offline",
                  last_error="ConnectionError: Tesla-Endpunkt http://192.0.2.33:8080 nicht erreichbar – läuft der Proxy?")
        wb["data"].update(state="idle", power=0.0)
    elif name == "error":
        pv, bat_w, soc = 2400.0, 0.0, 58.0
        wh["command_error"] = "Leistung 3000 W nicht übernommen (Gerät meldet 0 W)"
        s["warnings"] = ["Heizstab folgt der Regelung nicht"]
    elif name == "night":
        pv, house, bat_w, soc = 0.0, 380.0, -380.0, 46.0
        wb.update(presence="asleep_plugged")
        wb["data"].update(state="connected", vehicle_reachable=False, soc=72.0)
        wh["data"].update(temperature_c=49.0)
        s.update(price_ct=21.3)
    elif name == "grid_import":
        pv, house, bat_w, soc = 400.0, 2100.0, 0.0, 20.0
        s["battery_plan"] = {"intent": "no_discharge", "label": "Entladung gesperrt",
                             "reason": "Reserve 20 % erreicht", "source": "reserve"}
    elif name == "waste":
        pv, bat_w, soc = 7800.0, 0.0, 100.0
        wb.update(presence="away")
        wb["data"].update(state="idle", power=0.0, soc=None)
        wh["data"].update(temperature_c=60.0)
        s.update(waste_w=6900.0, waste_reason="Tesla Model Y: state=idle; Speicher voll")

    bat["data"].update(soc=soc, power=bat_w)
    s["battery"] = {"soc": soc, "power": bat_w}
    grid = house + car + water + bat_w - pv
    if name == "waste":
        grid = -s["waste_w"]
    if abs(grid) < 60:
        grid = 8.0
    s.update(pv_power=pv, house_power=house, wallbox_power=car, water_power=water, grid_power=grid,
             surplus=max(0.0, -grid + car + water))
    _dev(s, "inverter")["data"]["pv_power"] = pv
    _dev(s, "meter")["data"]["grid_power"] = grid
    return s


def _history(fields: list[str], hours: int) -> dict:
    """Plausibler Tagesverlauf: Sonne als Glocke, Haus mit Spitzen."""
    end = datetime.fromisoformat(NOON).astimezone(timezone.utc)
    step = timedelta(minutes=30 if hours > 24 else 10)
    n = int(timedelta(hours=hours) / step)
    out: dict[str, list] = {f: [] for f in fields}
    for i in range(n):
        t = end - step * (n - i)
        local_h = (t.hour + 2 + t.minute / 60) % 24
        sun = max(0.0, math.sin((local_h - 7) / 12 * math.pi)) * 7000
        house = 450 + 900 * (1 if 18 <= local_h <= 20 else 0) + 300 * math.sin(i / 3) ** 2
        vals = {"pv_power": sun, "house_power": house, "battery_power": max(-1500, min(2500, sun - house - 1500)),
                "grid_power": max(-3000, house - sun + 1500), "waste_power": max(0.0, sun - 6000),
                "surplus": max(0.0, sun - house), "battery_soc": 30 + 60 * max(0.0, math.sin((local_h - 8) / 16 * math.pi)),
                "water_power": 2000.0 if 11 <= local_h <= 13 else 0.0, "water_temp": 45 + 10 * math.sin(i / 20) ** 2}
        for f in fields:
            out[f].append({"time": t.isoformat(), "value": round(vals.get(f, 0.0), 1)})
    return out


STATS = {"range": "24h", "pv_kwh": 38.4, "grid_import_kwh": 6.2, "grid_export_kwh": 4.1, "consumption_kwh": 32.9,
         "autarky_pct": 81.2, "self_consumption_pct": 89.3, "charged_kwh": 14.2, "charged_solar_kwh": 12.8,
         "charged_solar_pct": 90.1, "savings_eur": 7.42, "co2_saved_kg": 11.3, "session_count": 2,
         "wasted_kwh": 1.4, "wasted_pct": 3.6, "wasted_value_eur": 0.42}


class Mock:
    def __init__(self, page, name: str = "sun", theme: str | None = None, freeze_time: bool = True,
                 unseen: bool = False):
        self.page = page
        self.unseen = unseen
        self.snapshot = scenario(name)
        self.writes: list[tuple[str, str, object]] = []
        self.sockets: list = []
        token = "mock-token"
        init = (f"localStorage.setItem('mp_token', {json.dumps(token)});"
                f"localStorage.setItem('mp_user', {json.dumps(json.dumps(USER))});")
        if theme:
            init += f"localStorage.setItem('mp_theme', '{theme}');"
        page.add_init_script(init)
        if freeze_time:
            page.clock.set_fixed_time(datetime.fromisoformat(NIGHT if name == "night" else NOON))
        page.route(re.compile(r".*/api/(?!ws).*"), self._handle)
        page.route_web_socket(re.compile(r".*/api/ws.*"), self._socket)

    # ------------------------------------------------------------- REST
    def _handle(self, route):
        req = route.request
        url = urlparse(req.url)
        path = url.path.split("/api/", 1)[1]
        key = path + (f"?{url.query}" if url.query else "")
        if req.method != "GET":
            try:
                body = req.post_data_json
            except Exception:  # noqa: BLE001
                body = req.post_data
            self.writes.append((req.method, path, body))
            m = re.fullmatch(r"devices/(\d+)", path)
            if m and req.method == "PATCH":
                dev = next((d for d in API.get("devices", []) if d["id"] == int(m.group(1))), {})
                merged = {**dev, **(body if isinstance(body, dict) else {})}
                return route.fulfill(status=200, json=merged)
            return route.fulfill(status=200, json={"ok": True})
        if path == "history" or path.startswith("history/"):
            q = parse_qs(url.query)
            fields = (q.get("fields", ["pv_power"])[0]).split(",")
            hours = {"6h": 6, "24h": 24, "7d": 168, "30d": 720}.get(q.get("range", ["24h"])[0], 24)
            if path == "history/energy":
                return route.fulfill(json=[])
            return route.fulfill(json=_history(fields, hours))
        if path == "statistics":
            return route.fulfill(json=STATS)
        if path == "devices/limits":
            return route.fulfill(json=LIMITS)
        if path == "system/changelog":
            entries = changelog()
            return route.fulfill(json={"version": entries[0]["version"], "entries": entries,
                                       "unseen": entries[:1] if self.unseen else []})
        for k in (key, path):
            if k in API:
                return route.fulfill(json=API[k])
        if path.startswith("events"):
            return route.fulfill(json=API.get("events?limit=200", []))
        return route.fulfill(status=404, json={"detail": f"mock: {key}"})

    # ------------------------------------------------------------- Live
    def _socket(self, ws):
        self.sockets.append(ws)
        ws.send(json.dumps({"type": "snapshot", "data": self.snapshot}))

    def push(self, snapshot: dict | None = None):
        """Neuen Snapshot an alle offenen Sockets schicken."""
        if snapshot is not None:
            self.snapshot = snapshot
        for ws in self.sockets:
            try:
                ws.send(json.dumps({"type": "snapshot", "data": self.snapshot}))
            except Exception:  # noqa: BLE001 – Socket schon zu
                pass

    def actions(self) -> list[tuple[str, str, object]]:
        """Nur Gerätebefehle, Batteriebefehle und Einstellungen."""
        return [w for w in self.writes if not w[1].startswith("auth/")]
