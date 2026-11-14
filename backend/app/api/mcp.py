"""KI-Assistenten über das Model Context Protocol (MCP).

* ``POST /mcp``: MCP-Server, Transport 'Streamable HTTP' (JSON-RPC 2.0, die
  Antwort kommt als application/json). Anmeldung per
  ``Authorization: Bearer mp_…`` mit einem Zugangsschlüssel aus den
  Einstellungen.
* ``/api/mcp/tokens``: Schlüssel anlegen, auflisten, widerrufen.

Jedes Werkzeug ruft intern die normale REST-API auf, mit den Rechten des
Kontos, höchstens aber mit der Rolle des Schlüssels. Damit gelten für eine
KI exakt dieselben Prüfungen (Feldprüfung, Gerätelimits, Rollen) wie für die
Oberfläche, und jede Aktion erscheint wie gewohnt im Ereignisprotokoll.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..core.logsafe import RedactFilter
from ..db import async_session, get_db
from ..models import ApiToken, User
from ..security import create_token, get_current_user
from ..services.audit import log_event

log = logging.getLogger(__name__)


#: Schlüssel in Adressen (/mcp/k/<Schlüssel>) nie ins Zugriffsprotokoll
#: schreiben – der Filter liegt zentral in core/logsafe.py.
RedactKeyFilter = RedactFilter

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
ROLE_RANK = {"readonly": 0, "user": 1, "admin": 2}
TOKEN_PREFIX = "mp_"

INSTRUCTIONS = (
    "MinePower regelt PV-Überschuss für Wallbox/Fahrzeug, Warmwasser und Hausbatterie. "
    "Leistungen in W (positiv = Bezug/Laden), Energie in kWh, Preise in ct/kWh, Zeiten lokal. "
    "Vor Änderungen zuerst lesen (get_live_status, list_devices). Gerät einrichten: list_drivers → "
    "Felder erfragen → test_device → add_device. Steuerbefehle wirken sofort; Rückfragen an den "
    "Nutzer vor riskanten Aktionen (Batterie entladen, Gerät löschen, Regelung pausieren)."
)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def effective_role(token_role: str, user_role: str) -> str:
    return min((token_role, user_role), key=lambda r: ROLE_RANK.get(r, 0))


# ---------------------------------------------------------------- interner API-Aufruf

class ApiError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


async def call_api(app, method: str, path: str, jwt: str, *, params: dict | None = None,
                   body: Any = None) -> Any:
    """Die eigene REST-API in-process aufrufen (ASGI), ohne Netzwerk."""
    payload = json.dumps(body).encode() if body is not None else b""
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None}, doseq=True)
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "root_path": "", "client": ("127.0.0.1", 0), "server": ("mcp.internal", 80),
        "headers": [(b"host", b"mcp.internal"), (b"authorization", f"Bearer {jwt}".encode()),
                    (b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
    }
    delivered = False
    never = asyncio.Event()

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await never.wait()
        return {"type": "http.disconnect"}

    status = 500
    ctype = ""
    chunks: list[bytes] = []

    async def send(message):
        nonlocal status, ctype
        if message["type"] == "http.response.start":
            status = message["status"]
            for key, value in message.get("headers", []):
                if key.lower() == b"content-type":
                    ctype = value.decode()
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    raw = b"".join(chunks)
    data: Any = raw.decode(errors="replace")
    if "json" in ctype and raw:
        data = json.loads(raw)
    if status >= 400:
        detail = data.get("detail", data) if isinstance(data, dict) else data
        raise ApiError(status, detail)
    return data


# ---------------------------------------------------------------- Werkzeuge

@dataclass
class Ctx:
    app: Any
    jwt: str
    role: str

    async def get(self, path: str, **params) -> Any:
        return await call_api(self.app, "GET", path, self.jwt, params=params)

    async def send(self, method: str, path: str, body: Any = None, **params) -> Any:
        return await call_api(self.app, method, path, self.jwt, params=params, body=body)


Handler = Callable[[Ctx, dict], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    role: str
    title: str
    description: str
    handler: Handler
    properties: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    destructive: bool = False

    def schema(self) -> dict:
        return {"type": "object", "properties": self.properties, "required": self.required,
                "additionalProperties": False}

    def describe(self) -> dict:
        read_only = self.role == "readonly"
        return {
            "name": self.name, "title": self.title, "description": self.description,
            "inputSchema": self.schema(),
            "annotations": {"title": self.title, "readOnlyHint": read_only,
                            "destructiveHint": self.destructive, "idempotentHint": read_only,
                            "openWorldHint": False},
        }


TOOLS: dict[str, Tool] = {}


def tool(name: str, role: str, title: str, description: str, properties: dict | None = None,
         required: list[str] | None = None, destructive: bool = False):
    def wrap(fn: Handler) -> Handler:
        TOOLS[name] = Tool(name, role, title, description, fn, properties or {}, required or [], destructive)
        return fn
    return wrap


INT = {"type": "integer"}
DEVICE_ID = {"type": "integer", "description": "Geräte-ID aus list_devices"}
RANGE_STATS = {"type": "string", "enum": ["24h", "7d", "30d", "365d"], "default": "30d"}
WALLBOX_MODES = ("pv_only", "min_pv", "pv_price", "target")
WATER_MODES = ("off", "pv_only", "pv_price", "schedule")
DAY_NAMES = ("mo", "di", "mi", "do", "fr", "sa", "so")


def _device_brief(d: dict) -> dict:
    data = d.get("data") or {}
    keep = ("power", "soc", "temperature_c", "state", "current_set", "phases_active", "grid_power", "pv_power",
            "is_on", "charge_limit_soc", "vehicle_reachable", "device_mode")
    out = {"id": d.get("id"), "name": d.get("name"), "category": d.get("category"), "online": d.get("online"),
           "mode": d.get("mode"), "override": d.get("override"), "boost": d.get("boost"),
           "data": {k: data[k] for k in keep if k in data and data[k] is not None},
           "decision": d.get("decision")}
    return {k: v for k, v in out.items() if v not in (None, {}, [])}


@tool("get_live_status", "readonly", "Aktueller Zustand",
      "Live-Werte der Anlage: PV, Netz (+ Bezug, − Einspeisung), Hausverbrauch, Batterie, Lasten, "
      "Strompreis, Überschuss, Warnungen und je Gerät Zustand und Regelentscheidung.")
async def _live(ctx: Ctx, args: dict) -> Any:
    s = await ctx.get("/api/status/live")
    keys = ("time", "pv_power", "grid_power", "house_power", "wallbox_power", "water_power", "battery",
            "surplus", "price_ct", "price_cheap", "price_limit_ct", "paused", "warnings", "waste_w",
            "waste_reason", "battery_plan", "battery_gate_reason", "battery_manual")
    out = {k: s.get(k) for k in keys if k in s}
    out["devices"] = [_device_brief(d) for d in s.get("devices", [])]
    return out


@tool("list_devices", "readonly", "Geräte",
      "Alle eingerichteten Geräte mit ID, Name, Klasse, Treiber, aktiv, Status und Einstellungen. "
      "Zugangsdaten sind maskiert. Enthält auch die Höchstzahl aktiver Geräte je Klasse.")
async def _devices(ctx: Ctx, args: dict) -> Any:
    return {"devices": await ctx.get("/api/devices"), "limits": await ctx.get("/api/devices/limits")}


@tool("get_battery", "readonly", "Batterie",
      "Batteriezustand: Ladestand, Leistung, Modus im Wechselrichter, Grenzen, laufender Handbefehl.")
async def _battery(ctx: Ctx, args: dict) -> Any:
    return await ctx.get("/api/battery/status")


@tool("get_history", "readonly", "Verlauf",
      "Zeitreihen. Felder: pv_power, grid_power, house_power, wallbox_power, water_power, battery_power, "
      "battery_soc, surplus, waste_power, price_ct, water_temp.",
      {"fields": {"type": "array", "items": {"type": "string"},
                  "default": ["pv_power", "grid_power", "house_power", "battery_soc"]},
       "range": {"type": "string", "enum": ["1h", "6h", "24h", "7d", "30d"], "default": "24h"}})
async def _history(ctx: Ctx, args: dict) -> Any:
    fields = args.get("fields") or ["pv_power", "grid_power", "house_power", "battery_soc"]
    return await ctx.get("/api/history", fields=",".join(fields), range=args.get("range", "24h"))


@tool("get_statistics", "readonly", "Kennzahlen",
      "Kennzahlen für einen Zeitraum: PV, Bezug, Einspeisung, Autarkie, Eigenverbrauch, geladene Energie, "
      "Solaranteil, Ersparnis, verschenkter Solarstrom.", {"range": RANGE_STATS})
async def _stats(ctx: Ctx, args: dict) -> Any:
    return await ctx.get("/api/statistics", range=args.get("range", "30d"))


def _csv_summary(text: str) -> dict:
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if len(rows) <= 1:
        return {"sessions": 0}

    def num(value: str) -> float:
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return 0.0

    body = rows[1:]
    energy = sum(num(r[3]) for r in body if len(r) > 4)
    solar = sum(num(r[4]) for r in body if len(r) > 4)
    cost = sum(num(r[6]) for r in body if len(r) > 6)
    return {"sessions": len(body), "energy_kwh": round(energy, 2), "solar_kwh": round(solar, 2),
            "solar_pct": round(100 * solar / energy, 1) if energy else 0.0, "cost_eur": round(cost, 2)}


@tool("get_charge_sessions", "readonly", "Ladeliste",
      "Ladevorgänge als CSV (wie der Export in der App, Semikolon, Dezimalkomma) samt Summen, "
      "oder als JSON-Liste.",
      {"range": RANGE_STATS, "format": {"type": "string", "enum": ["csv", "json"], "default": "csv"},
       "limit": {"type": "integer", "default": 100, "description": "nur JSON"}})
async def _sessions(ctx: Ctx, args: dict) -> Any:
    if args.get("format") == "json":
        return await ctx.get("/api/sessions", limit=int(args.get("limit") or 100))
    text = await ctx.get("/api/statistics/export.csv", range=args.get("range", "30d"))
    return {"summary": _csv_summary(text), "csv": text}


@tool("get_events", "readonly", "Ereignisse",
      "Ereignisprotokoll, neueste zuerst. Kategorien: control, battery, waste, device, config, auth.",
      {"limit": {"type": "integer", "default": 50}, "category": {"type": "string"}})
async def _events(ctx: Ctx, args: dict) -> Any:
    return await ctx.get("/api/events", limit=min(int(args.get("limit") or 50), 500), category=args.get("category"))


@tool("get_findings", "readonly", "Diagnose-Befunde",
      "Automatische Befunde der Diagnose mit Ursache und Abhilfe (Geräte offline, verschenkter Strom …).")
async def _findings(ctx: Ctx, args: dict) -> Any:
    return await ctx.get("/api/system/findings")


@tool("get_tariff", "readonly", "Strompreise",
      "Dynamische Strompreise (ct/kWh) der nächsten Stunden und die eingestellte Preisgrenze.")
async def _tariff(ctx: Ctx, args: dict) -> Any:
    return await ctx.get("/api/statistics/tariff")


@tool("get_forecast", "readonly", "PV-Prognose", "PV-Ertragsprognose, falls eingerichtet.")
async def _forecast(ctx: Ctx, args: dict) -> Any:
    return await ctx.get("/api/statistics/forecast")


@tool("get_settings", "readonly", "Einstellungen",
      "Einstellungen, optional nur ein Abschnitt (z. B. regulation, tariff, notifications, ui). "
      "Zugangsdaten sind maskiert.", {"section": {"type": "string"}})
async def _settings(ctx: Ctx, args: dict) -> Any:
    values = (await ctx.get("/api/settings")).get("values", {})
    section = args.get("section")
    if section:
        if section not in values:
            raise ApiError(404, f"Abschnitt '{section}' unbekannt. Vorhanden: {', '.join(sorted(values))}")
        return {section: values[section]}
    return values


@tool("list_schedules", "readonly", "Zeitfenster",
      "Zeitfenster für Warmwasser im Modus 'schedule'. days_mask: Bit 0 = Montag … Bit 6 = Sonntag.",
      {"device_id": DEVICE_ID})
async def _schedules(ctx: Ctx, args: dict) -> Any:
    items = await ctx.get("/api/settings/schedules")
    if args.get("device_id") is not None:
        items = [s for s in items if s.get("device_id") == args["device_id"]]
    return items


@tool("list_drivers", "readonly", "Treiberkatalog",
      "Verfügbare Treiber je Klasse (inverter, meter, battery, wallbox, water_heater) mit Konfigurationsfeldern. "
      "Grundlage für add_device.", {"category": {"type": "string",
                                                  "enum": ["inverter", "meter", "battery", "wallbox", "water_heater"]}})
async def _drivers(ctx: Ctx, args: dict) -> Any:
    metas = await ctx.get("/api/drivers", category=args.get("category"))
    out = []
    for m in metas:
        out.append({
            "id": m["id"], "name": m["name"], "category": m["category"], "description": m.get("description"),
            "maturity": m.get("maturity"), "notes": m.get("notes"), "capabilities": m.get("capabilities"),
            "fields": [{k: f.get(k) for k in ("key", "label", "type", "required", "default", "options", "placeholder", "help")
                        if f.get(k) not in (None, "", [])} for f in m.get("fields", [])],
        })
    return out


# --- Steuern (Rolle 'user') -------------------------------------------------

async def _action(ctx: Ctx, device_id: int, action: str, value: Any = None) -> Any:
    return await ctx.send("POST", f"/api/devices/{int(device_id)}/action", {"action": action, "value": value})


@tool("set_mode", "user", "Betriebsart setzen",
      "Betriebsart eines Geräts. Wallbox/Fahrzeug: pv_only (nur PV), min_pv (Mindeststrom + PV), "
      "pv_price (PV, unter Preisgrenze auch Netz), target (Zielladung). Warmwasser: off, pv_only, pv_price, "
      "schedule (Zeitfenster).", {"device_id": DEVICE_ID, "mode": {"type": "string"}}, ["device_id", "mode"])
async def _set_mode(ctx: Ctx, args: dict) -> Any:
    mode = str(args["mode"])
    if mode not in WALLBOX_MODES + WATER_MODES:
        raise ApiError(422, f"Unbekannte Betriebsart '{mode}'")
    return await _action(ctx, args["device_id"], "set_mode", mode)


@tool("vehicle_command", "user", "Laden sofort/aus/automatisch",
      "Wallbox/Fahrzeug: fast = sofort mit voller Leistung (auch Netz), stop = Laden aus, "
      "auto = zurück zur Betriebsart, wake = Fahrzeug wecken.",
      {"device_id": DEVICE_ID, "command": {"type": "string", "enum": ["fast", "stop", "auto", "wake"]}},
      ["device_id", "command"])
async def _vehicle(ctx: Ctx, args: dict) -> Any:
    return await _action(ctx, args["device_id"], args["command"])


@tool("set_charge_limit", "user", "Ladegrenze",
      "Ladegrenze im Fahrzeug in Prozent (50–100), sofern der Treiber das unterstützt.",
      {"device_id": DEVICE_ID, "soc": {"type": "integer", "minimum": 50, "maximum": 100}}, ["device_id", "soc"])
async def _limit(ctx: Ctx, args: dict) -> Any:
    return await _action(ctx, args["device_id"], "set_charge_limit", int(args["soc"]))


@tool("water_boost", "user", "Warmwasser-Boost",
      "Boost starten (volle Leistung bis Boost-Temperatur/Dauer laut Geräteeinstellung) oder beenden.",
      {"device_id": DEVICE_ID, "active": {"type": "boolean"}}, ["device_id", "active"])
async def _boost(ctx: Ctx, args: dict) -> Any:
    return await _action(ctx, args["device_id"], "boost" if args["active"] else "boost_off")


@tool("battery_command", "user", "Batterie von Hand",
      "Batterie zeitlich begrenzt steuern: charge (laden, auch aus dem Netz), discharge (entladen), "
      "no_discharge (Entladung sperren), hold (weder laden noch entladen), auto (zurück zur Automatik).",
      {"action": {"type": "string", "enum": ["auto", "charge", "discharge", "no_discharge", "hold"]},
       "minutes": {"type": "number", "description": "Dauer in Minuten"},
       "power_w": {"type": "number", "description": "Leistung für charge/discharge"},
       "target_soc": {"type": "number", "description": "Ziel-Ladestand in %"},
       "device_id": DEVICE_ID}, ["action"], destructive=True)
async def _battery_cmd(ctx: Ctx, args: dict) -> Any:
    body = {k: args[k] for k in ("action", "minutes", "power_w", "target_soc", "device_id") if args.get(k) is not None}
    return await ctx.send("POST", "/api/battery/manual", body)


@tool("pause_regulation", "user", "Regelung pausieren",
      "Regelung anhalten: Wallbox und Warmwasser werden sicher abgeschaltet.", destructive=True)
async def _pause(ctx: Ctx, args: dict) -> Any:
    return await ctx.send("POST", "/api/status/pause")


@tool("resume_regulation", "user", "Regelung fortsetzen", "Pausierte Regelung wieder starten.")
async def _resume(ctx: Ctx, args: dict) -> Any:
    return await ctx.send("POST", "/api/status/resume")


# --- Einrichten (Rolle 'admin') ---------------------------------------------

def _days_mask(days: Any) -> int:
    if days in (None, "", "daily", "täglich"):
        return 127
    if days in ("workdays", "werktags"):
        return 31
    if days in ("weekend", "wochenende"):
        return 96
    if isinstance(days, int):
        return days & 127
    mask = 0
    for d in days:
        key = str(d).strip().lower()[:2]
        key = {"mo": "mo", "tu": "di", "di": "di", "we": "mi", "mi": "mi", "th": "do", "do": "do", "fr": "fr",
               "sa": "sa", "su": "so", "so": "so"}.get(key, key)
        if key not in DAY_NAMES:
            raise ApiError(422, f"Unbekannter Wochentag '{d}'")
        mask |= 1 << DAY_NAMES.index(key)
    return mask


@tool("create_schedule", "admin", "Zeitfenster anlegen",
      "Zeitfenster für Warmwasser (Modus 'schedule'): volle Leistung bis Zieltemperatur. Über Mitternacht erlaubt.",
      {"device_id": DEVICE_ID, "start_time": {"type": "string", "pattern": "^\\d{2}:\\d{2}$"},
       "end_time": {"type": "string", "pattern": "^\\d{2}:\\d{2}$"},
       "days": {"description": "'daily', 'workdays', 'weekend' oder Liste wie ['mo','mi','fr']",
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}},
      ["device_id", "start_time", "end_time"])
async def _create_schedule(ctx: Ctx, args: dict) -> Any:
    body = {"device_id": int(args["device_id"]), "start_time": args["start_time"], "end_time": args["end_time"],
            "days_mask": _days_mask(args.get("days"))}
    return await ctx.send("POST", "/api/settings/schedules", body)


@tool("delete_schedule", "admin", "Zeitfenster löschen", "Ein Zeitfenster löschen.",
      {"schedule_id": INT}, ["schedule_id"], destructive=True)
async def _delete_schedule(ctx: Ctx, args: dict) -> Any:
    return await ctx.send("DELETE", f"/api/settings/schedules/{int(args['schedule_id'])}")


@tool("set_priority", "admin", "Reihenfolge der Lasten",
      "Reihenfolge, in der Wallboxen und Warmwasser Überschuss bekommen (erste ID zuerst).",
      {"order": {"type": "array", "items": INT}}, ["order"])
async def _priority(ctx: Ctx, args: dict) -> Any:
    return await ctx.send("PUT", "/api/settings/priority", {"order": [int(i) for i in args["order"]]})


@tool("test_device", "admin", "Verbindung testen",
      "Verbindung mit einem Treiber und einer Konfiguration testen, ohne etwas zu speichern. "
      "Liefert gelesene Werte oder die Fehlerursache.",
      {"driver_id": {"type": "string"}, "config": {"type": "object"}, "device_id": DEVICE_ID},
      ["driver_id", "config"])
async def _test(ctx: Ctx, args: dict) -> Any:
    body = {"driver_id": args["driver_id"], "config": args.get("config") or {}, "device_id": args.get("device_id"),
            "include_write": False}
    return await ctx.send("POST", "/api/devices/test", body)


@tool("add_device", "admin", "Gerät hinzufügen",
      "Gerät anlegen. Felder laut list_drivers. Die Konfiguration wird geprüft; vorher test_device ausführen.",
      {"name": {"type": "string"}, "category": {"type": "string",
                                                "enum": ["inverter", "meter", "battery", "wallbox", "water_heater"]},
       "driver_id": {"type": "string"}, "config": {"type": "object"}, "settings": {"type": "object"},
       "enabled": {"type": "boolean", "default": True}}, ["name", "category", "driver_id", "config"])
async def _add(ctx: Ctx, args: dict) -> Any:
    body = {k: args[k] for k in ("name", "category", "driver_id", "config", "settings", "enabled") if k in args}
    return await ctx.send("POST", "/api/devices", body)


@tool("update_device", "admin", "Gerät ändern",
      "Name, Konfiguration, Einstellungen oder Aktiv-Status eines Geräts ändern. Maskierte Zugangsdaten "
      "('•••') bleiben unverändert.",
      {"device_id": DEVICE_ID, "name": {"type": "string"}, "config": {"type": "object"},
       "settings": {"type": "object"}, "enabled": {"type": "boolean"}}, ["device_id"])
async def _update(ctx: Ctx, args: dict) -> Any:
    body = {k: args[k] for k in ("name", "config", "settings", "enabled") if k in args}
    return await ctx.send("PATCH", f"/api/devices/{int(args['device_id'])}", body)


@tool("delete_device", "admin", "Gerät löschen",
      "Gerät endgültig löschen. Nur mit confirm=true. Zum vorübergehenden Abschalten update_device mit enabled=false.",
      {"device_id": DEVICE_ID, "confirm": {"type": "boolean"}}, ["device_id", "confirm"], destructive=True)
async def _delete(ctx: Ctx, args: dict) -> Any:
    if args.get("confirm") is not True:
        raise ApiError(422, "Löschen nur mit confirm=true")
    return await ctx.send("DELETE", f"/api/devices/{int(args['device_id'])}")


@tool("update_settings", "admin", "Einstellungen ändern",
      "Werte eines Einstellungsabschnitts ändern (z. B. section='regulation', values={'grid_target_w': 0}). "
      "Nicht genannte Werte bleiben erhalten.",
      {"section": {"type": "string"}, "values": {"type": "object"}}, ["section", "values"])
async def _update_settings(ctx: Ctx, args: dict) -> Any:
    section = str(args["section"])
    current = (await ctx.get("/api/settings")).get("values", {}).get(section) or {}
    if not isinstance(current, dict):
        raise ApiError(422, f"Abschnitt '{section}' ist kein Objekt")
    merged = {**current, **(args.get("values") or {})}
    await ctx.send("PUT", "/api/settings", {"values": {section: merged}})
    return {section: merged}


PROMPTS = {
    "geraet_einrichten": {
        "title": "Gerät einrichten",
        "description": "Schritt für Schritt ein neues Gerät hinzufügen.",
        "text": "Hilf mir, ein Gerät in MinePower einzurichten. Frage nach der Geräteklasse und dem Hersteller, "
                "suche mit list_drivers den passenden Treiber, frage die Pflichtfelder einzeln ab, teste mit "
                "test_device und lege es erst nach erfolgreichem Test mit add_device an. Erkläre Fehler knapp.",
    },
    "tagesbericht": {
        "title": "Tagesbericht",
        "description": "Kurzer Bericht über Erzeugung, Verbrauch und Auffälligkeiten.",
        "text": "Erstelle einen kurzen Bericht über die letzten 24 Stunden: get_statistics (24h), get_history, "
                "get_findings und get_charge_sessions. Nenne PV-Ertrag, Autarkie, geladene Energie, verschenkten "
                "Solarstrom und konkrete Verbesserungen.",
    },
}


# ---------------------------------------------------------------- JSON-RPC

def _result(rid: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def visible_tools(role: str) -> list[Tool]:
    rank = ROLE_RANK.get(role, 0)
    return [t for t in TOOLS.values() if ROLE_RANK[t.role] <= rank]


async def handle_rpc(msg: dict, ctx: Ctx, token_name: str) -> dict | None:
    rid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if not isinstance(method, str):
        return _error(rid, -32600, "Ungültige Anfrage")
    if rid is None:
        return None  # Benachrichtigung (z. B. notifications/initialized)
    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(rid, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
            "serverInfo": {"name": "MinePower", "title": "MinePower", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": [t.describe() for t in visible_tools(ctx.role)]})
    if method == "prompts/list":
        return _result(rid, {"prompts": [{"name": k, "title": v["title"], "description": v["description"]}
                                         for k, v in PROMPTS.items()]})
    if method == "prompts/get":
        p = PROMPTS.get(str(params.get("name")))
        if p is None:
            return _error(rid, -32602, "Unbekannter Prompt")
        return _result(rid, {"description": p["description"],
                             "messages": [{"role": "user", "content": {"type": "text", "text": p["text"]}}]})
    if method in ("resources/list", "resources/templates/list"):
        return _result(rid, {"resources": []} if method == "resources/list" else {"resourceTemplates": []})
    if method == "tools/call":
        name = params.get("name")
        t = TOOLS.get(str(name))
        if t is None:
            return _error(rid, -32602, f"Unbekanntes Werkzeug: {name}")
        if ROLE_RANK[t.role] > ROLE_RANK.get(ctx.role, 0):
            return _result(rid, _tool_text(f"Keine Berechtigung: '{name}' braucht die Rolle '{t.role}'.", True))
        args = params.get("arguments") or {}
        missing = [k for k in t.required if k not in args]
        if missing:
            return _result(rid, _tool_text(f"Fehlende Parameter: {', '.join(missing)}", True))
        try:
            data = await t.handler(ctx, args)
        except ApiError as exc:
            return _result(rid, _tool_text(_error_text(exc), True))
        except Exception as exc:  # noqa: BLE001 – der KI eine Ursache liefern statt abzubrechen
            log.exception("MCP-Werkzeug %s fehlgeschlagen", name)
            return _result(rid, _tool_text(f"Interner Fehler: {type(exc).__name__}", True))
        if t.role != "readonly":
            await log_event(f"KI-Assistent ({token_name}): {t.title}", category="mcp",
                            data={"tool": t.name, "arguments": _redact(args)})
        return _result(rid, _tool_payload(data))
    return _error(rid, -32601, f"Methode nicht unterstützt: {method}")


def _error_text(exc: ApiError) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        fields = detail.get("fields")
        msg = detail.get("message") or json.dumps(detail, ensure_ascii=False)
        if fields:
            msg += " (" + "; ".join(f"{f.get('label') or f.get('key')}: {f.get('error')}" for f in fields) + ")"
        return f"Fehler {exc.status}: {msg}"
    return f"Fehler {exc.status}: {detail}"


def _redact(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k == "config" and isinstance(v, dict):
            out[k] = {ck: ("•••" if any(s in ck.lower() for s in ("pass", "token", "secret", "key")) else cv)
                      for ck, cv in v.items()}
        else:
            out[k] = v
    return out


def _tool_text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _tool_payload(data: Any) -> dict:
    if isinstance(data, str):
        return _tool_text(data)
    text = json.dumps(data, ensure_ascii=False, default=str)
    result = _tool_text(text)
    if isinstance(data, dict):
        result["structuredContent"] = data
    return result


# ---------------------------------------------------------------- HTTP-Endpunkt

def _header_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else None


async def _token_user(token: str | None, db: AsyncSession) -> tuple[User, ApiToken] | None:
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    row = await db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    if row is None:
        return None
    user = await db.get(User, row.user_id)
    if user is None or user.disabled:
        return None
    now = datetime.now(timezone.utc)
    last = row.last_used_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is None or now - last > timedelta(minutes=1):
        row.last_used_at = now
        await db.commit()
    return user, row


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "Zugangsschlüssel fehlt oder ungültig"}, status_code=401,
                        headers={"WWW-Authenticate": 'Bearer realm="MinePower"'})


@router.post("/mcp", include_in_schema=False)
async def mcp_endpoint(request: Request):
    return await _serve(request, _header_token(request))


@router.post("/mcp/k/{key}", include_in_schema=False)
async def mcp_endpoint_url_key(key: str, request: Request):
    """Für Anwendungen, die nur eine Adresse kennen (Konnektoren in Grok,
    Claude, ChatGPT …): Schlüssel im Pfad. Die Kopfzeile hat Vorrang."""
    return await _serve(request, _header_token(request) or key)


async def _serve(request: Request, token: str | None):
    async with async_session() as db:
        auth = await _token_user(token, db)
        if auth is None:
            return _unauthorized()
        user, token = auth
        role = effective_role(token.role, user.role)
        token_name = token.name
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(_error(None, -32700, "Kein gültiges JSON"), status_code=400)
    ctx = Ctx(app=request.app, jwt=create_token(user), role=role)
    batch = isinstance(body, list)
    messages = body if batch else [body]
    replies = []
    for msg in messages:
        if not isinstance(msg, dict):
            replies.append(_error(None, -32600, "Ungültige Anfrage"))
            continue
        reply = await handle_rpc(msg, ctx, token_name)
        if reply is not None:
            replies.append(reply)
    if not replies:
        return Response(status_code=202)
    return JSONResponse(replies if batch else replies[0])


@router.get("/mcp", include_in_schema=False)
@router.get("/mcp/k/{key}", include_in_schema=False)
async def mcp_get(key: str | None = None):
    # Kein Server-zu-Client-Stream: Antworten kommen direkt auf den POST.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp", include_in_schema=False)
@router.delete("/mcp/k/{key}", include_in_schema=False)
async def mcp_delete(key: str | None = None):
    return Response(status_code=405, headers={"Allow": "POST"})


# ---------------------------------------------------------------- Schlüssel verwalten

class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = "readonly"


def _token_out(row: ApiToken) -> dict:
    return {"id": row.id, "name": row.name, "prefix": row.prefix, "role": row.role,
            "created_at": row.created_at, "last_used_at": row.last_used_at}


@router.get("/api/mcp/tokens")
async def list_tokens(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (await db.scalars(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id))).all()
    return [_token_out(r) for r in rows]


@router.post("/api/mcp/tokens")
async def create_api_token(body: TokenCreate, db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    if body.role not in ROLE_RANK:
        raise HTTPException(422, "Rolle muss readonly, user oder admin sein")
    if ROLE_RANK[body.role] > ROLE_RANK.get(user.role, 0):
        raise HTTPException(403, "Rolle des Schlüssels höher als die des Kontos")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(user_id=user.id, name=body.name.strip(), token_hash=hash_token(token), prefix=token[:7],
                   role=body.role)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await log_event(f"KI-Zugang angelegt: {row.name} ({row.role})", category="auth", user_id=user.id)
    return {**_token_out(row), "token": token}


@router.delete("/api/mcp/tokens/{token_id}")
async def revoke_token(token_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    row = await db.get(ApiToken, token_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Schlüssel nicht gefunden")
    await db.delete(row)
    await db.commit()
    await log_event(f"KI-Zugang widerrufen: {row.name}", category="auth", user_id=user.id)
    return {"ok": True}


@router.get("/api/mcp/tools")
async def list_tools(_: User = Depends(get_current_user)):
    return [{"name": t.name, "title": t.title, "role": t.role, "description": t.description}
            for t in TOOLS.values()]
