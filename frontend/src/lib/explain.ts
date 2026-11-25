/** 'Warum passiert gerade was?" – die Regel-Entscheidungen in Sätzen.
 *
 *  Der Regelkreis liefert je Gerät eine knappe technische Begründung
 *  ('warte auf Überschuss (0 W)", 'Batterie hat Vorrang (68 % < 100 %)").
 *  Hier wird daraus eine Überschrift (was passiert) und eine kurze Liste
 *  (warum), in Alltagssprache und mit den Zahlen, auf die es ankommt.
 */
import { tr, type T } from "../i18n";
import { fmtCt, fmtDuration, fmtTemp, fmtW } from "./format";
import type { Snapshot, SnapshotDevice } from "./types";
import { HEAT_ICON, heatKind } from "./heat";
import type { IconName } from "../components/icons";

export type Tone = "pv" | "import" | "export" | "battery" | "car" | "water" | "warn" | "idle" | "err";

export type Reason = { key: string; icon: IconName; text: string; tone?: Tone };

export type Explanation = { headline: string; tone: Tone; reasons: Reason[] };

const num = (d: SnapshotDevice | undefined, key: string): number | null => {
  const v = d?.data?.[key];
  return typeof v === "number" ? v : null;
};

function reasonOf(d: SnapshotDevice | undefined): string {
  const r = d?.decision?.reason;
  return typeof r === "string" ? r : "";
}

/** Zahl in Klammern am Ende einer Begründung: 'warte auf Überschuss (1234 W)". */
function wattsIn(reason: string): number | null {
  const m = reason.match(/\((-?\d+) W\)/);
  return m ? Number(m[1]) : null;
}

/** 'Überschuss reicht (4067 W) – Start in 40 s' → 40 (Startverzögerung läuft). */
/** Fehlertext kurz für Banner und Listen: Proxy-Adresse oder der erste Satz
 *  ohne Fehlerklasse. Der volle Text steht in der Diagnose. */
export function shortError(error: string | null | undefined): string {
  if (!error) return "";
  const host = error.match(/https?:\/\/([\w.-]+(?::\d+)?)/)?.[1];
  if (/proxy/i.test(error) && host) return tr("explain.hostDown", { host });
  return error.replace(/^[A-Za-z]+(Error|Exception|Problem|Rejected)?: /, "").split(" – ")[0].slice(0, 120);
}

export function startIn(reason: string): number | null {
  const m = reason.match(/Start in (\d+) s/);
  return m ? Number(m[1]) : null;
}

/** Die technische Begründung fürs Einspeisen ('Batterie lädt mit 3957 W – sie
 *  hat Vorrang; Wallbox: warte auf Überschuss (4067 W)") in kurze Teilsätze. */
export function wasteCauses(raw: string, t: T, skip: string[] = []): string[] {
  const out: string[] = [];
  for (const part of raw.split(";").map((x) => x.trim()).filter(Boolean)) {
    // Geräte, die oben schon einen eigenen Satz haben, nicht doppelt nennen
    if (skip.some((name) => part.startsWith(`${name}:`) || part === `${name} offline`)) continue;
    let m: RegExpMatchArray | null;
    if (/^Batterie lädt .*Vorrang/.test(part)) out.push(t("explain.wBatteryFirst"));
    else if (/^Batterie entlädt/.test(part)) out.push(t("explain.wBatteryDischarging"));
    else if ((m = part.match(/^(.+?): .*Start in (\d+) s/))) out.push(t("explain.wStarting", { name: m[1], sec: m[2] }));
    else if ((m = part.match(/^(.+?): warte auf Überschuss/))) out.push(t("explain.wWaiting", { name: m[1] }));
    else if ((m = part.match(/^(.+?): .*Zieltemperatur/))) out.push(t("explain.wHot", { name: m[1] }));
    else if ((m = part.match(/^(.+?): .*(nicht ladebereit|keine Leistung|nicht angesteckt|unplugged)/i))) out.push(t("explain.wNotReady", { name: m[1] }));
    else if ((m = part.match(/^(.+?) offline$/))) out.push(t("explain.wOffline", { name: m[1] }));
    else if (/Keine steuerbare Last/.test(part)) out.push(t("explain.wNoLoads"));
    else if (/Alle steuerbaren Lasten sind offline/.test(part)) out.push(t("explain.wAllOffline"));
    else if (/am Maximum/.test(part)) out.push(t("explain.wAllMax"));
    else out.push(part.replace(/\s*\(-?\d+ W\)/g, ""));
  }
  return out;
}

/** Begründungen, die Auto und Warmwasser gemeinsam haben, solange (noch)
 *  keine Leistung fließt – etwa im Takt zwischen Befehl und Messwert. */
function commonReason(reason: string, kind: "car" | "water", t: T): { text: string; tone: Tone } | null {
  const k = (name: string) => `explain.${kind}${name}`;
  let m: RegExpMatchArray | null;
  if ((m = reason.match(/^PV-Überschuss (\d+) W/))) return { text: t(k("SwitchingOn"), { power: fmtW(Number(m[1])) }), tone: "pv" };
  if (/^Netzladen/.test(reason)) return { text: t(k("GridStarting")), tone: "import" };
  if ((m = reason.match(/Phasenumschaltung → (\d)p/))) return { text: t("explain.carPhases", { n: m[1] }), tone: "car" };
  if (/Stopp-Verzögerung/.test(reason)) return { text: t(k("StopSoon")), tone: "idle" };
  if (/Wolkendurchzug/.test(reason)) return { text: t(k("Cloud")), tone: "pv" };
  if (/außerhalb Zeitfenster/.test(reason)) return { text: t(k("OutsideWindow")), tone: "idle" };
  if (/^Zeitfenster aktiv/.test(reason)) return { text: t(k("Window")), tone: "water" };
  if (/^deaktiviert/.test(reason)) return { text: t(k("Disabled")), tone: "idle" };
  if (/^manuell gestoppt/.test(reason)) return { text: t("explain.carStopped"), tone: "idle" };
  return null;
}

export type CarState =
  | "none" | "offline" | "proxy" | "unknown" | "asleep" | "away"
  | "unplugged" | "charging" | "waiting" | "complete";

export function carState(d: SnapshotDevice | undefined): CarState {
  if (!d) return "none";
  switch (d.presence) {
    case "proxy_offline": return "proxy";
    case "offline": return "offline";
    case "unknown": return "unknown";
    case "asleep_plugged": return "asleep";
    case "away": return "away";
    case "unplugged": return "unplugged";
    case "charging": return "charging";
    case "complete": return "complete";
    case "plugged": return "waiting";
    default: break;
  }
  // ältere Backends ohne 'presence'
  if (!d.online) return /proxy|8080/i.test(d.last_error ?? "") ? "proxy" : "offline";
  if (d.data?.vehicle_reachable === false) return "asleep";
  const state = String(d.data?.state ?? "idle");
  const power = num(d, "power") ?? 0;
  if (state === "idle" || state === "error") return "unplugged";
  if (state === "charging" && power > 100) return "charging";
  if (state === "complete") return "complete";
  return "waiting";
}

/** Ein Satz zum Zustand des Autos. */
export function carSentence(d: SnapshotDevice, t: T): { text: string; tone: Tone } {
  const state = carState(d);
  const reason = reasonOf(d);
  const power = num(d, "power") ?? 0;
  switch (state) {
    case "proxy":
      return { text: t("explain.carProxyDown"), tone: "err" };
    case "offline":
      return { text: t("explain.carOffline"), tone: "err" };
    case "unknown":
      return { text: t("explain.carUnknown"), tone: "idle" };
    case "asleep":
      return { text: t("explain.carAsleep"), tone: "idle" };
    case "away":
      return { text: t("explain.carAway"), tone: "idle" };
    case "unplugged":
      return { text: t("explain.carUnplugged"), tone: "idle" };
    case "complete":
      return { text: t("explain.carComplete"), tone: "idle" };
    case "charging": {
      if (d.override === "fast") return { text: t("explain.carFast", { power: fmtW(power) }), tone: "car" };
      if (/Netzladen/.test(reason)) return { text: t("explain.carGrid", { power: fmtW(power) }), tone: "import" };
      return { text: t("explain.carSolar", { power: fmtW(power) }), tone: "pv" };
    }
    default: {
      if (d.override === "stop") return { text: t("explain.carStopped"), tone: "idle" };
      if (/Vorrang/.test(reason)) return { text: t("explain.carBatteryFirst"), tone: "battery" };
      const starting = startIn(reason);
      if (starting != null) return { text: t("explain.carStarting", { sec: starting }), tone: "pv" };
      if (/warte auf Überschuss/.test(reason) || /Überschuss zu gering/.test(reason)) {
        const avail = wattsIn(reason);
        return {
          text: t("explain.carWaiting", {
            min: fmtW(d.start_threshold_w ?? d.min_power_w ?? 1400),
            avail: fmtW(Math.max(0, avail ?? 0)),
          }),
          tone: "idle",
        };
      }
      if (/nicht ladebereit|keine Leistung|^state=/.test(reason)) return { text: t("explain.carNotReady"), tone: "warn" };
      const other = commonReason(reason, "car", t);
      if (other) return other;
      return { text: reason ? t("explain.carOther", { reason }) : t("explain.carIdle"), tone: "idle" };
    }
  }
}

/** Ein Satz zum Zustand des Warmwassers. */
export function waterSentence(d: SnapshotDevice, t: T): { text: string; tone: Tone } {
  const reason = reasonOf(d);
  const power = num(d, "power") ?? 0;
  const temp = num(d, "temperature_c");
  if (!d.online) return { text: t("explain.waterOffline"), tone: "err" };
  if (typeof d.data?.device_mode === "string" && d.data.device_mode) {
    return { text: t("explain.waterOwnProgram", { power: fmtW(power) }), tone: "warn" };
  }
  if (d.boost) {
    const rem = d.boost_state?.remaining_s;
    return {
      text: rem != null && d.boost_state?.end_mode !== "temp"
        ? t("explain.waterBoostTime", { time: fmtDuration(rem) })
        : t("explain.waterBoostTemp", { temp: fmtTemp(d.boost_state?.target_temp_c ?? d.boost_temp_c) }),
      tone: "water",
    };
  }
  if (/Wärmepuffer voll/.test(reason)) return { text: t("explain.waterBufferFull", { temp: fmtTemp(temp) }), tone: "idle" };
  if (/Wärmepuffer/.test(reason)) return { text: t("explain.waterBuffer", { power: fmtW(power) }), tone: "pv" };
  if (/Zieltemperatur/.test(reason)) {
    return { text: t("explain.waterHot", { temp: fmtTemp(temp), target: fmtTemp(d.target_temp_c) }), tone: "idle" };
  }
  if (power > 50 && /Netzladen/.test(reason)) return { text: t("explain.waterGrid", { power: fmtW(power) }), tone: "import" };
  if (power > 50) return { text: t("explain.waterSolar", { power: fmtW(power) }), tone: "pv" };
  if (/Vorrang/.test(reason)) return { text: t("explain.waterBatteryFirst"), tone: "battery" };
  const starting = startIn(reason);
  if (starting != null) return { text: t("explain.waterStarting", { sec: starting }), tone: "pv" };
  if (/warte auf Überschuss/.test(reason)) return { text: t("explain.waterWaiting"), tone: "idle" };
  if (d.mode === "off") return { text: t("explain.waterOff"), tone: "idle" };
  const other = commonReason(reason, "water", t);
  if (other) return other;
  return { text: reason ? t("explain.waterOther", { reason }) : t("explain.waterIdle"), tone: "idle" };
}

/** Wärmepumpe/Klimaanlage (regelt selbst): MinePower gibt frei oder nicht –
 *  was das Gerät ohne Freigabe zieht, ist sein eigener Normalbetrieb. */
export function heatPumpSentence(d: SnapshotDevice, t: T): { text: string; tone: Tone } {
  const reason = reasonOf(d);
  const power = num(d, "power") ?? 0;
  const name = d.name;
  const released = Number((d.decision as { power_w?: number } | undefined)?.power_w ?? 0) > 0;
  if (!d.online) return { text: t("explain.hpOffline", { name }), tone: "err" };
  if (d.boost) {
    const rem = d.boost_state?.remaining_s;
    return { text: t("explain.hpBoost", { name, time: rem != null ? fmtDuration(rem) : "–" }), tone: "water" };
  }
  const left = /(Mindestlaufzeit|Mindestpause) – noch (\d+) min/.exec(reason);
  if (left && left[1] === "Mindestlaufzeit") return { text: t("explain.hpMinRun", { name, min: left[2] }), tone: "pv" };
  if (released) {
    if (/Netzladen|Zeitfenster/.test(reason)) return { text: t("explain.hpGrid", { name, power: fmtW(power) }), tone: "import" };
    return power > 50
      ? { text: t("explain.hpSolar", { name, power: fmtW(power) }), tone: "pv" }
      : { text: t("explain.hpReleased", { name }), tone: "pv" };
  }
  if (left) return { text: t("explain.hpMinPause", { name, min: left[2] }), tone: "idle" };
  if (d.mode === "off") return { text: t("explain.hpOff", { name }), tone: "idle" };
  if (/Vorrang/.test(reason)) return { text: t("explain.hpBatteryFirst", { name }), tone: "battery" };
  const starting = startIn(reason);
  if (starting != null) return { text: t("explain.hpStarting", { name, sec: starting }), tone: "pv" };
  if (power > 50) return { text: t("explain.hpOwn", { name, power: fmtW(power) }), tone: "idle" };
  return { text: t("explain.hpWaiting", { name }), tone: "idle" };
}

/** Satz für jede Wärmelast – Heizstab/Warmwasser oder selbstregelnd. */
export function heatSentence(d: SnapshotDevice, t: T, named = false): { text: string; tone: Tone } {
  if (d.self_regulating) return heatPumpSentence(d, t);
  const w = waterSentence(d, t);
  return named ? { ...w, text: `${d.name}: ${w.text}` } : w;
}

/** Läuft die Wärmelast auf Anweisung von MinePower (nicht im eigenen Betrieb)? */
export function heatControlledOn(d: SnapshotDevice): boolean {
  const p = num(d, "power") ?? 0;
  if (p <= 50) return false;
  if (!d.self_regulating) return true;
  return Number((d.decision as { power_w?: number } | undefined)?.power_w ?? 0) > 0;
}

export function explain(s: Snapshot, t: T): Explanation {
  const reasons: Reason[] = [];
  const cars = s.devices.filter((d) => d.category === "wallbox" && d.enabled);
  const heats = s.devices.filter((d) => d.category === "water_heater" && d.enabled);
  const car = cars[0];
  const water = heats.find((d) => !d.self_regulating) ?? heats[0];
  // Was MinePower an Wärme gerade steuert (ohne Eigenbetrieb einer Wärmepumpe)
  const heatOn = heats.filter(heatControlledOn);
  const heatW = heatOn.reduce((sum, d) => sum + (num(d, "power") ?? 0), 0);
  const heatKindOn = heatOn.length === 1 && heatOn[0].self_regulating ? heatOn[0] : null;
  const grid = s.grid_power ?? 0;
  const bat = s.battery?.power ?? 0;
  const ownProgram = water && typeof water.data?.device_mode === "string" && water.data.device_mode;

  // --- Überschrift ------------------------------------------------------
  let headline = t("explain.hIdle");
  let tone: Tone = "idle";
  if (s.paused) {
    headline = t("explain.hPaused");
    tone = "warn";
  } else if (s.safety) {
    headline = t("explain.hSafety");
    tone = "err";
  } else if (s.wallbox_power > 50 && heatW > 50) {
    headline = heatKindOn ? t("explain.hBothHeat", { name: heatKindOn.name }) : t("explain.hBoth");
    tone = grid > 300 ? "import" : "pv";
  } else if (s.wallbox_power > 50) {
    headline = grid > s.wallbox_power * 0.5 ? t("explain.hCarGrid") : t("explain.hCarSolar");
    tone = grid > s.wallbox_power * 0.5 ? "import" : "car";
  } else if (heatW > 50 && heatKindOn) {
    headline = grid > heatW * 0.5 ? t("explain.hHeatGrid", { name: heatKindOn.name })
      : t("explain.hHeatSolar", { name: heatKindOn.name });
    tone = grid > heatW * 0.5 ? "import" : "water";
  } else if (heatW > 50) {
    headline = ownProgram ? t("explain.hWaterOwn") : grid > heatW * 0.5 ? t("explain.hWaterGrid") : t("explain.hWaterSolar");
    tone = ownProgram ? "warn" : grid > heatW * 0.5 ? "import" : "water";
  } else if (s.battery_grid_charging) {
    headline = t("explain.hBatteryGrid");
    tone = "import";
  } else if (bat > 100) {
    headline = t("explain.hBatterySolar");
    tone = "battery";
  } else if (s.waste_w > 150) {
    headline = t("explain.hExport");
    tone = "export";
  } else if (bat < -100) {
    headline = t("explain.hBatteryHouse");
    tone = "battery";
  } else if (grid > 100) {
    headline = t("explain.hImport");
    tone = "import";
  } else if (s.pv_power > 50) {
    headline = t("explain.hSolarHouse");
    tone = "pv";
  }

  // --- Gründe -------------------------------------------------------------
  if (s.paused) reasons.push({ key: "paused", icon: "pause", text: t("explain.paused"), tone: "warn" });
  if (s.safety) reasons.push({ key: "safety", icon: "warning", text: s.safety, tone: "err" });

  if (s.battery_manual) {
    const m = s.battery_manual;
    const what = m.intent ? t(`bat.act.${m.intent}`) : t(m.mode === "force_charge" ? "bat.act.charge" : "bat.act.discharge");
    reasons.push({
      key: "manual", icon: "battery", tone: "battery",
      text: t("explain.batManual", { what, time: fmtDuration(m.remaining_s) }),
    });
  } else if (s.battery_plan && s.battery_plan.intent !== "auto") {
    reasons.push({
      key: "plan", icon: "battery", tone: s.battery_plan.intent === "charge" ? "import" : "battery",
      text: `${t("explain.battery")}: ${s.battery_plan.reason}`,
    });
  }

  if (s.price_ct != null) {
    const limit = s.price_limit_ct ?? null;
    reasons.push({
      key: "price", icon: "price", tone: s.price_cheap ? "pv" : "idle",
      text: s.price_cheap
        ? t("explain.priceCheap", { price: fmtCt(s.price_ct), limit: fmtCt(limit) })
        : t("explain.priceHigh", { price: fmtCt(s.price_ct), limit: fmtCt(limit) }),
    });
  }

  // Jedes Gerät bekommt seine Zeile – bei mehreren derselben Art mit Namen.
  for (const c of cars) {
    const sentence = carSentence(c, t);
    reasons.push({ key: `car-${c.id}`, icon: "car", tone: sentence.tone,
                   text: cars.length > 1 ? `${c.name}: ${sentence.text}` : sentence.text });
  }
  const waterCount = heats.filter((d) => !d.self_regulating).length;
  for (const h of heats) {
    const sentence = heatSentence(h, t, waterCount > 1);
    reasons.push({ key: `heat-${h.id}`, icon: HEAT_ICON[heatKind(h)], text: sentence.text, tone: sentence.tone });
  }

  // Angezeigt wird die aktuelle Einspeisung – dieselbe Zahl wie oben im
  // Netz-Chip. `waste_w` ist geglättet und dient nur als Auslöser, damit ein
  // kurzer Ausschlag keine Meldung erzeugt.
  const exportW = Math.max(0, -grid);
  if (s.waste_w > 150 && exportW > 150) {
    const shown = [...cars, ...heats].map((d) => d.name).filter((x): x is string => !!x);
    const causes = s.waste_reason ? wasteCauses(s.waste_reason, t, shown) : [];
    reasons.push({
      key: "waste", icon: "export", tone: "export",
      text: causes.length
        ? t("explain.wasteWhy", { power: fmtW(exportW), reason: causes.join(", ") })
        : t("explain.waste", { power: fmtW(exportW) }),
    });
  }

  return { headline, tone, reasons };
}
