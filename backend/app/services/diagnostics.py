"""Automatische Befunde für die Diagnose-Seite und den Diagnosebericht.

Jeder Befund beantwortet drei Fragen in Klartext:

* **Was** ist los (Titel),
* **warum** – die wahrscheinliche Ursache, mit den Zahlen, die darauf
  hindeuten (`detail`),
* **was tun** – ein konkreter Schritt (`fix`).

Zwei Quellen:

1. Der *Zustand* jetzt (Loop, Geräte, Einstellungen) – etwa 'Netzladen an,
   aber Batterie nicht steuerbar".
2. Das *Verhalten* über die letzten Tage (Ereignisse, Kennzahlen, Verlauf) –
   etwa 'ELWA folgt fast täglich um 6 Uhr nicht der Regelung". Genau das
   erkannte die Auswertung bisher nicht: Der Bericht vom 03.10. meldete nur
   'Regeltakt 20 s", während das Protokoll voller wiederkehrender Warnungen
   stand.

Reine Funktionen über die übergebenen Daten: testbar ohne Datenbank und ohne
laufende Hardware.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ..drivers.base import BatteryData, BatteryMode, DeviceCategory

#: Ab diesem Takt fühlen sich Bedienbefehle ohne Sofort-Takt träge an.
SLOW_INTERVAL_S = 10.0
#: Ab wann verschenkte Energie ein eigener Befund ist.
WASTE_WARN_KWH = 2.0
WASTE_WARN_PCT = 5.0

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2, "ok": 3}


def _tz():
    try:
        return ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
    except Exception:  # noqa: BLE001 – unbekannte Zeitzone → Berlin
        return ZoneInfo("Europe/Berlin")


def _finding(level: str, title: str, detail: str, fix: str | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"level": level, "title": title, "detail": detail}
    if fix:
        out["fix"] = fix
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def mask_vin(value: Any) -> Any:
    """Fahrgestellnummer auf Anfang und Ende kürzen – reicht zur
    Zuordnung, ist aber kein vollständiges personenbezogenes Merkmal mehr."""
    if not isinstance(value, str) or len(value) < 8:
        return value
    return f"{value[:3]}…{value[-4:]}"


# ---------------------------------------------------------------- Hilfen


def _hours_text(times: list[datetime]) -> str | None:
    """'meist gegen 06 Uhr und 17 Uhr" – wenn sich die Fälle um wenige
    Uhrzeiten (Ortszeit) häufen. Sonst None."""
    if len(times) < 3:
        return None
    tz = _tz()
    hours = Counter(t.astimezone(tz).hour for t in times)
    top = hours.most_common(3)
    covered = sum(n for _h, n in top)
    if covered / len(times) < 0.6:
        return None
    labels = sorted(h for h, _n in top if _n >= 2) or [top[0][0]]
    if len(labels) == 1:
        return f"meist gegen {labels[0]:02d} Uhr"
    return "meist gegen " + ", ".join(f"{h:02d}" for h in labels[:-1]) + f" und {labels[-1]:02d} Uhr"


def _device_name(message: str, marker: str) -> str | None:
    if marker not in message:
        return None
    return message.split(marker)[0].strip() or None


def _per_day(count: int, events: list) -> float:
    if not events:
        return 0.0
    times = [e.time for e in events]
    span_days = max(1.0, (max(times) - min(times)).total_seconds() / 86400.0)
    return count / span_days


# ---------------------------------------------------------------- Zustand


def _state_findings(loop, devices: list, settings: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cfg = loop.cfg
    managed = {d.id: d for d in loop.devices.values()}

    # --- doppelt angelegte Geräte ---------------------------------------
    seen: dict[tuple, str] = {}
    for row in devices:
        conf = row.config or {}
        addr = tuple(str(conf.get(k)) for k in ("host", "port", "unit_id", "vin", "url"))
        if not any(a not in ("None", "") for a in addr):
            continue
        key = (row.driver_id, addr)
        if key in seen:
            out.append(_finding(
                "error", "Gerät doppelt angelegt",
                f"'{row.name}' (ID {row.id}) und '{seen[key]}' haben dieselbe Adresse – Werte zählen doppelt.",
                "Duplikat unter Einstellungen → Geräte löschen.",
            ))
        else:
            seen[key] = f"{row.name} (ID {row.id})"

    # --- Batterie ----------------------------------------------------------
    batteries = [d for d in managed.values() if d.category == DeviceCategory.BATTERY.value and d.enabled]
    controllable = [d for d in batteries if loop.battery_controllable(d)]
    if cfg.battery_grid_charge_enabled and not controllable:
        out.append(_finding(
            "error", "Netzladen an, Batterie nicht steuerbar",
            "Es wird nie ein Ladebefehl gesendet.",
            "Geräte → Batterie → 'Aktive Batteriesteuerung erlauben'."
            if batteries else "Batteriegerät anlegen (z. B. 'Sungrow Batterie SBR/SBH').",
        ))
    for dev in batteries:
        data = dev.data if isinstance(dev.data, BatteryData) else None
        if data is None:
            continue
        if data.mode_known and data.mode != BatteryMode.AUTO and not loop.battery_controllable(dev):
            out.append(_finding(
                "error", f"{dev.name} hängt im Modus '{data.mode.value}'",
                "MinePower darf den Modus nicht zurücksetzen.",
                "Modus in der Hersteller-App prüfen oder aktive Steuerung freischalten.",
            ))
        if data.max_soc is not None and data.max_soc < 50:
            out.append(_finding(
                "error", f"{dev.name}: Ladegrenze im Wechselrichter {data.max_soc:.0f} %",
                "Die Batterie lädt nicht darüber.",
                "Max-SoC in der Hersteller-App auf 100 % stellen.",
            ))
        if data.max_soc is not None and cfg.battery_grid_charge_enabled \
                and cfg.battery_grid_charge_soc > data.max_soc + 0.5:
            out.append(_finding(
                "warn", "Netzladeziel über der Ladegrenze",
                f"Ziel {cfg.battery_grid_charge_soc:.0f} %, Wechselrichter lädt bis {data.max_soc:.0f} %.",
                f"Netzladen bis höchstens {data.max_soc:.0f} % einstellen.",
            ))
        held = getattr(dev, "inverter_reserve", None)
        if loop.battery_controllable(dev) and (dev.settings or {}).get("manage_reserve", True):
            if held is not None and held + 0.5 < cfg.battery_reserve_soc:
                out.append(_finding(
                    "info", f"Reserve: Wechselrichter hält {held:.0f} %, MinePower den Rest",
                    f"Der Wechselrichter nimmt höchstens {held:.0f} % an; bis {cfg.battery_reserve_soc:.0f} % "
                    f"sperrt MinePower das Entladen selbst.",
                ))
            err = getattr(dev, "inverter_reserve_error", None)
            if err:
                out.append(_finding("warn", f"{dev.name}: Reserve nicht übernommen", err,
                                    "Schreibjournal prüfen; MinePower hält die Reserve solange selbst."))
        if dev.command_error:
            out.append(_finding("error", f"{dev.name}: Befehl nicht übernommen", dev.command_error,
                                "Schreibjournal prüfen (Register, Wert, Antwort)."))

    # --- Phasen: eingestellt vs. gemessen --------------------------------
    for dev in managed.values():
        ctrl = dev.controller
        if dev.category != DeviceCategory.WALLBOX.value or ctrl is None:
            continue
        seen_phases = getattr(ctrl, "_phases_seen", None)
        configured = {"fixed1": 1, "fixed3": 3}.get(getattr(ctrl.s, "phases_mode", ""))
        if seen_phases and configured and seen_phases != configured:
            out.append(_finding(
                "info", f"{dev.name} lädt {seen_phases}-phasig (eingestellt {configured})",
                f"MinePower rechnet automatisch mit {seen_phases} Phasen, Start ab "
                f"{ctrl._min_power() / 1000:.1f} kW.",
                f"Optional: Auto → Erweitert → Phasen auf '{seen_phases}-phasig'.",
            ))

    # --- Preisquelle --------------------------------------------------------
    tariff = getattr(loop, "tariff", None)
    price_modes = [
        d.name for d in managed.values()
        if d.enabled and getattr(getattr(d.controller, "s", None), "mode", None) in ("price", "pv_price")
    ]
    provider = getattr(tariff, "provider", "none") if tariff else "none"
    tariff_cfg = settings.get("tariff") or {}
    if (price_modes or cfg.battery_grid_charge_enabled) and provider == "none":
        out.append(_finding(
            "warn", "Preisoptimierung ohne Strompreis",
            f"Kein Tarif eingestellt – betroffen: {', '.join(price_modes) or 'Batterie'}.",
            "Tarif → Anbieter wählen.",
        ))
    elif tariff is not None and provider != "none":
        status = tariff.status() if hasattr(tariff, "status") else {}
        if status and not status.get("has_current_price", True):
            out.append(_finding(
                "warn", "Kein aktueller Strompreis",
                f"{status.get('last_error') or 'Keine Daten'} – bis dahin kein Netzladen.",
                "Token bzw. Internetverbindung prüfen.",
            ))
        comps = [float(tariff_cfg.get(k) or 0) for k in ("grid_fees_ct", "levies_ct", "supplier_margin_ct", "vat_pct")]
        if provider == "awattar" and not any(comps):
            out.append(_finding(
                "warn", "Aufschläge auf den Börsenpreis fehlen",
                "aWATTar liefert den Börsenpreis; die Grenze vergleicht so gegen einen zu niedrigen Preis.",
                "Tarif → Aufschläge aus der Stromrechnung eintragen.",
            ))
        elif provider == "tibber" and not any(comps):
            out.append(_finding(
                "ok", "Tarif: Aufschläge 0 sind richtig",
                "Tibber liefert den Endpreis.",
            ))

    # --- Geräte offline / Takt -----------------------------------------------
    for dev in managed.values():
        if dev.enabled and not dev.online and dev.failures:
            out.append(_finding("warn", f"{dev.name} nicht erreichbar", _short_error(dev.last_error or ""),
                                _offline_fix(dev.driver_id, dev.last_error or "")))
    if cfg.interval_s > SLOW_INTERVAL_S:
        out.append(_finding(
            "info", f"Regeltakt {cfg.interval_s:.0f} s",
            "Die Automatik reagiert träge auf Wolken.",
            "Einstellungen → Regelung → Takt 10 s.",
        ))
    return out


def _short_error(error: str) -> str:
    """Fehlertext kurz: Proxy-Adresse oder der erste Satz ohne Fehlerklasse."""
    if not error:
        return "keine Antwort"
    host = re.search(r"https?://([\w.-]+(?::\d+)?)", error)
    if host and ("nicht erreichbar" in error.lower() or "proxy" in error.lower()):
        return f"{host.group(1)} antwortet nicht."
    text = re.sub(r"^[A-Za-z]+(Error|Exception|Problem|Rejected)?: ", "", error)
    return text.split(" – ")[0][:140]


def _meta_attr(driver_id: str, attr: str):
    from ..drivers import registry

    try:
        return getattr(registry.get_driver_class(driver_id).meta, attr)
    except KeyError:
        return None


def _offline_fix(driver_id: str, error: str) -> str:
    """Abhilfe aus den Treiber-Metadaten; sonst nach Fehlerart."""
    low = error.lower()
    hint = _meta_attr(driver_id, "offline_hint")
    if hint:
        return hint
    if "modbus" in low or "unit-id" in low or "502" in low:
        return "IP, Port 502 und 'Modbus TCP' im Gerät prüfen."
    return "Strom, Netzwerk und IP des Geräts prüfen."


# ---------------------------------------------------------------- Verhalten

#: Ereignistexte vor und seit 2.16 (beide müssen erkannt werden)
_OWN_MARKERS = (" heizt im eigenen Programm", " heizt per Geräteprogramm")
_SELF_START_MARKERS = (" hat selbst angefangen zu laden", " startet selbst")
_UNREACHABLE_PREFIX = "Fahrzeug antwortet nicht mehr: "
_UNREACHABLE_SUFFIX = " antwortet nicht ("


def _match(message: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        name = _device_name(message, marker)
        if name:
            return name
    return None


def _behaviour_findings(events: list, devices: list, loop) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_name = {d.name: d for d in devices}

    disobey: dict[str, list] = defaultdict(list)
    own_program: dict[str, list] = defaultdict(list)
    self_start: dict[str, list] = defaultdict(list)
    for e in events:
        name = _device_name(e.message, " folgt der Regelung nicht")
        if name:
            disobey[name].append(e)
        name = _match(e.message, _OWN_MARKERS)
        if name:
            own_program[name].append(e)
        name = _match(e.message, _SELF_START_MARKERS)
        if name:
            self_start[name].append(e)

    for name, evs in disobey.items():
        dev = by_name.get(name)
        driver = getattr(dev, "driver_id", "") or ""
        pattern = _hours_text([e.time for e in evs])
        when = f" ({pattern})" if pattern else ""
        # Ältere Protokolle kennen nur 'folgt nicht" – beim Heizstab ist das
        # fast immer das Geräteprogramm, beim Auto der Selbststart.
        if "own_program" in (_meta_attr(driver, "capabilities") or set()):
            own_program[name] = sorted(own_program.get(name, []) + evs, key=lambda e: e.time)
            continue
        if getattr(dev, "category", None) == DeviceCategory.WALLBOX.value:
            self_start[name] = sorted(self_start.get(name, []) + evs, key=lambda e: e.time)
            continue
        out.append(_finding(
            "warn", f"{name} folgt der Regelung nicht",
            f"{len(evs)}×{when}.",
            "Externe Steuerung im Gerät prüfen; Befehlsfehler auf der Diagnose-Seite ansehen.",
            count=len(evs),
        ))

    for name, evs in own_program.items():
        pattern = _hours_text([e.time for e in evs])
        dev = by_name.get(name)
        hint = _meta_attr(getattr(dev, "driver_id", "") or "", "own_program_hint")
        out.append(_finding(
            "info", f"{name} heizt per Geräteprogramm",
            f"{len(evs)}×{' (' + pattern + ')' if pattern else ''}. Batterie wird dabei nicht entladen.",
            hint or "Geräteprogramm in den Geräteeinstellungen prüfen.",
            count=len(evs),
        ))
    for name, evs in self_start.items():
        pattern = _hours_text([e.time for e in evs])
        out.append(_finding(
            "info", f"{name} startet selbst",
            f"{len(evs)}×{' (' + pattern + ')' if pattern else ''}, meist beim Anstecken – jeweils sofort gestoppt.",
            "'Geplantes Laden' in der Fahrzeug-App prüfen.",
            count=len(evs),
        ))

    # --- Offline / Erreichbarkeit -----------------------------------------
    offline: dict[str, list] = defaultdict(list)
    unreachable: dict[str, list] = defaultdict(list)
    for e in events:
        if e.message.startswith("Gerät offline: "):
            name = e.message[len("Gerät offline: "):].split(" (")[0]
            offline[name].append(e)
        if e.message.startswith(_UNREACHABLE_PREFIX):
            name = e.message[len(_UNREACHABLE_PREFIX):].split(".")[0]
            unreachable[name].append(e)
        elif _UNREACHABLE_SUFFIX in e.message:
            unreachable[e.message.split(_UNREACHABLE_SUFFIX)[0]].append(e)
    for name, evs in offline.items():
        dev = by_name.get(name)
        last = evs[-1].message
        proxy = "TeslaBleHttpProxy" in last or "Proxy" in last
        host = re.search(r"https?://([\d.]+(?::\d+)?)", last)
        out.append(_finding(
            "warn" if len(evs) >= 2 else "info",
            f"{name}: {'BLE-Proxy' if proxy else 'Gerät'} {len(evs)}× offline",
            (f"Proxy {host.group(1)} nicht erreichbar." if proxy and host else last[:160]),
            _offline_fix(getattr(dev, "driver_id", "") or "", last),
            count=len(evs),
        ))
    for name, evs in unreachable.items():
        rate = _per_day(len(evs), evs)
        out.append(_finding(
            "info", f"{name}: {len(evs)}× länger nicht erreichbar",
            f"Ø {rate:.1f}× am Tag (unterwegs oder angesteckt eingeschlafen). Angesteckt wird es bei "
            f"Überschuss geweckt.",
            "Zu Hause oft nicht erreichbar? Abstand BLE-Proxy ↔ Auto verkleinern.",
            count=len(evs),
        ))
    return out


#: Gründe fürs Verschenken, die sich vermeiden lassen (Schlüsselwörter)
AVOIDABLE_WASTE = ("manuell gestoppt", "offline", "nicht erreichbar", "nicht ladebereit",
                   "zieltemperatur", "startschwelle", "pause nach stopp")


def waste_is_avoidable(reason: str) -> bool:
    """Ließe sich dieser Grund durch Einstellung oder Bedienung vermeiden?

    Unvermeidbar: Speicher voll bzw. lädt am Limit und nichts anderes kann
    abnehmen (Auto nicht da/voll, alle Lasten am Maximum). Vermeidbar: ein
    vergessenes 'Aus', ein Gerät offline, ein schlafendes Auto, Wasser am
    Ziel ohne Wärmepuffer."""
    low = reason.lower()
    if "wärmepuffer voll" in low:
        return False
    return any(k in low for k in AVOIDABLE_WASTE)


def _waste_findings(summary: dict | None, events: list) -> list[dict[str, Any]]:
    if not summary:
        return []
    kwh = float(summary.get("wasted_kwh") or 0)
    pct = float(summary.get("wasted_pct") or 0)
    if kwh < WASTE_WARN_KWH and pct < WASTE_WARN_PCT:
        return []
    reasons: Counter = Counter()
    avoidable = unavoidable = 0.0
    for e in events:
        if e.category != "waste":
            continue
        data = e.data or {}
        share = float(data.get("kwh") or 0)
        texts = [str(r) for r in (data.get("reasons") or [])] or [e.message]
        flag = data.get("avoidable")
        if flag is None:
            flag = any(waste_is_avoidable(t) for t in texts[:2])
        if flag:
            avoidable += share
        else:
            unavoidable += share
        for r in texts[:2]:
            key = re.sub(r"\d+([.,]\d+)?", "#", r.split(" – ")[0])
            reasons[key] += share
    top = [k.replace("#", "…") for k, _v in reasons.most_common(3)]
    split = (f"vermeidbar {avoidable:.1f} kWh, unvermeidbar {unavoidable:.1f} kWh"
             if avoidable or unavoidable else "")
    text = " ".join(top).lower()
    fixes = []
    if "zieltemperatur" in text:
        fixes.append("Warmwasser → 'Mit Überschuss heizen bis' z. B. 70 °C")
    if "manuell gestoppt" in text:
        fixes.append("'Aus' am Auto endet jetzt automatisch")
    if "nicht erreichbar" in text or "offline" in text:
        fixes.append("Auto angesteckt lassen – MinePower weckt es bei Überschuss")
    if not fixes:
        fixes.append("Details je Episode unter Ereignisse → Verschenkt")
    return [_finding(
        "warn" if avoidable >= WASTE_WARN_KWH or pct >= 15 else "info",
        f"{kwh:.1f} kWh Solarstrom verschenkt ({pct:.0f} %)",
        "; ".join(x for x in (split, ("Gründe: " + "; ".join(top)) if top else "") if x) + ".",
        "; ".join(fixes) + ".",
        kwh=round(kwh, 1), avoidable_kwh=round(avoidable, 1),
    )]


def _reserve_findings(history: dict | None, loop, devices: list) -> list[dict[str, Any]]:
    if not history:
        return []
    socs = [p.get("value") for p in (history.get("battery_soc") or []) if p.get("value") is not None]
    if not socs:
        return []
    low = min(socs)
    reserve = float(loop.cfg.battery_reserve_soc)
    if low >= reserve - 2:
        return []
    managed = [d for d in loop.devices.values() if d.category == DeviceCategory.BATTERY.value]
    holding = any(getattr(d, "inverter_reserve", None) is not None for d in managed)
    return [_finding(
        "info", f"Batterie fiel auf {low:.0f} % (Reserve {reserve:.0f} %)",
        "Ab 2.16 hält MinePower die Reserve selbst und schreibt sie in den Wechselrichter."
        if holding else "Der Wechselrichter entlud bis zu seiner eigenen Untergrenze.",
        None if holding else "Batterie aktiv steuern lassen (Geräte → Batterie), dann gilt die Reserve.",
    )]


# ---------------------------------------------------------------- Einstieg


def findings(
    loop,
    devices: list,
    settings: dict[str, Any],
    events: Iterable | None = None,
    summary: dict | None = None,
    history: dict | None = None,
) -> list[dict[str, Any]]:
    """Alle Befunde, wichtigste zuerst."""
    events = sorted(list(events or []), key=lambda e: e.time)
    out = _state_findings(loop, devices, settings)
    out += _behaviour_findings(events, devices, loop)
    out += _waste_findings(summary, events)
    out += _reserve_findings(history, loop, devices)
    out.sort(key=lambda f: LEVEL_ORDER.get(f["level"], 9))
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
