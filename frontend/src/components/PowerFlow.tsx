/** Energiefluss: Woher kommt der Strom, wohin geht er?
 *
 *  Aufbau wie in den Hersteller-Apps: Jedes Gerät steht als isometrisches
 *  Bild auf einer Kachel. Oben die Sonne, links das Netz, rechts der
 *  Speicher, in der Mitte das Haus – und darunter die Verbraucher, die das
 *  Haus versorgt: jedes Auto und jede Wärmelast (Warmwasser, Wärmepumpe,
 *  Klimaanlage) mit eigenem Bild und eigener Leitung.
 *
 *  Bis vier Verbraucher stehen einzeln in der unteren Reihe; mehr werden ab
 *  dem vierten zu „+n weitere“ zusammengefasst, damit die Zahlen lesbar
 *  bleiben.
 *
 *  Die Leitungen sind dynamisch:
 *   - sie erscheinen farbig nur, wenn wirklich Strom fließt,
 *   - Pfeil und laufende Punkte zeigen die Richtung (Einspeisen, Laden …),
 *   - je mehr Leistung, desto dicker die Leitung und desto schneller die Punkte.
 *  Ohne Bewegungswunsch (prefers-reduced-motion) stehen die Punkte still,
 *  die Richtung zeigt dann der Pfeil.
 *
 *  Bilder und Leitungen liegen in einem gemeinsamen Koordinatensystem
 *  (400 × 510) mit festem Seitenverhältnis; die Zahlen darüber sind HTML,
 *  damit Schrift auf jedem Bildschirm gleich groß und scharf bleibt.
 */
import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";
import { useI18n, type T } from "../i18n";
import { fmtPct, fmtTemp, fmtW } from "../lib/format";
import { HEAT_LABEL, HEAT_TONE, devPower, devTemp, heatKind } from "../lib/heat";
import type { Snapshot, SnapshotDevice } from "../lib/types";
import { BatteryArt, CarArt, ClimateArt, GridArt, HeatPumpArt, HouseArt, PvArt, WaterArt } from "./FlowArt";
import { Icon, type IconName } from "./icons";

type Dir = "fwd" | "rev" | "none";

const VW = 400;
const VH = 510;
const ART_W = 124;               // Bildbreite in Diagramm-Einheiten
const HOUSE_W = 144;
const RATIO = 100 / 120;         // Höhe/Breite eines Bildes
/** Höchstens so viele Verbraucher einzeln; der Rest wird zusammengefasst. */
export const MAX_CONSUMERS = 4;

/** Bildmitte der festen Knoten. */
const POS = {
  pv: [200, 56],
  grid: [68, 154],
  battery: [332, 154],
  house: [200, 284],
} as const;

const PATH = {
  pv: "M 200 160 V 238",
  grid: "M 126 218 H 164 Q 182 218 182 236",
  battery: "M 274 218 H 236 Q 218 218 218 236",
};

const ROW_Y = 396;

/** Animationsdauer aus Leistung: 1 kW ≈ 2,4 s je Durchlauf, 10 kW ≈ 0,7 s. */
const duration = (w: number) => Math.max(0.6, Math.min(3.2, 3.2 - Math.log10(Math.max(w, 50) / 50) * 1.15));

/** Leitungsstärke aus Leistung: 2 (kaum) bis 5,5 (≥ 11 kW). */
const width = (w: number) => Math.max(2, Math.min(5.5, 2 + Math.log10(Math.max(w, 50) / 50) * 1.5));

type Badge = { icon: IconName; text: string; tone: "ok" | "warn" | "info" };

type NodeInfo = {
  key: string;
  x: number;
  y: number;
  w: number;
  path: string;
  watts: number;
  dir: Dir;
  tone: string;
  value: string;
  sub: string;
  badge?: Badge;
  absent?: boolean;
  alert?: boolean;
  link?: string;
  art: ReactNode;
};

const pct = (v: number, of: number) => `${(v / of) * 100}%`;

/** x-Positionen der unteren Reihe: links und rechts vom Haus, nie in der
 *  Mitte (dort steht die Hausbeschriftung). */
export function consumerSlots(n: number): { xs: number[]; w: number } {
  if (n <= 0) return { xs: [], w: ART_W };
  if (n === 1) return { xs: [68], w: ART_W };
  if (n === 2) return { xs: [68, 332], w: ART_W };
  const left = Math.ceil(n / 2);
  const right = n - left;
  const side = (count: number, a: number, b: number, single: number) => (count === 1 ? [single] : [a, b]);
  return { xs: [...side(left, 50, 150, 68), ...side(right, 250, 350, 332)], w: 96 };
}

/** Leitung vom Haus zur Bildkante eines Verbrauchers. */
function consumerPath(x: number, y: number, w: number): string {
  const end = y - (w * RATIO) / 2 + w * RATIO * 0.17;
  if (x < 200) {
    if (x >= 150) return `M 150 322 V ${end}`;
    return `M 150 322 H ${x + 28} Q ${x} 322 ${x} 350 V ${end}`;
  }
  if (x <= 250) return `M 250 322 V ${end}`;
  return `M 250 322 H ${x - 28} Q ${x} 322 ${x} 350 V ${end}`;
}

type Consumer = {
  key: string;
  watts: number;
  tone: string;
  value: string;
  sub: string;
  badge?: Badge;
  absent?: boolean;
  link?: string;
  art: (on: boolean) => ReactNode;
};

function carConsumer(t: T, car: SnapshotDevice | undefined): Consumer {
  const watts = devPower(car);
  const soc = typeof car?.data?.soc === "number" ? (car.data.soc as number) : null;
  const plugged = car ? (car.data?.state ?? "idle") !== "idle" : false;
  return {
    key: car ? `car-${car.id}` : "car",
    watts, tone: "car",
    value: car ? fmtW(watts) : "–",
    sub: car?.name && car.name.length <= 18 ? car.name : t("flow.car"),
    badge: soc != null ? { icon: "car", text: fmtPct(soc), tone: "ok" } : undefined,
    absent: !car,
    link: car ? `/auto#dev-${car.id}` : undefined,
    art: (on) => <CarArt active={on} present={!car || plugged || on} />,
  };
}

function heatConsumer(t: T, dev: SnapshotDevice | undefined): Consumer {
  const kind = heatKind(dev);
  const watts = devPower(dev);
  const temp = devTemp(dev);
  const label = kind === "water" ? t("flow.water") : t(HEAT_LABEL[kind]);
  const cooling = String(dev?.data?.extra && (dev.data.extra as Record<string, unknown>)["Betriebsart"] || "") === "Kühlen";
  return {
    key: dev ? `heat-${dev.id}` : "water",
    watts, tone: HEAT_TONE[kind],
    value: dev ? fmtW(watts) : "–",
    sub: dev?.name && dev.name.length <= 18 ? dev.name : label,
    badge: temp != null ? { icon: "thermo", text: fmtTemp(temp), tone: "info" } : undefined,
    absent: !dev,
    link: dev ? `/warmwasser#dev-${dev.id}` : undefined,
    art: (on) => kind === "heat_pump" || kind === "heating" ? <HeatPumpArt active={on} temp={temp} />
      : kind === "climate" ? <ClimateArt active={on} cooling={cooling} />
      : <WaterArt active={on} temp={temp} />,
  };
}

/** Verbraucher in Anzeigereihenfolge (Autos, dann Wärme), höchstens
 *  MAX_CONSUMERS – darüber wird der Rest zu einem Knoten zusammengefasst. */
export function consumers(t: T, snapshot: Snapshot): Consumer[] {
  const cars = snapshot.devices.filter((d) => d.category === "wallbox" && d.enabled);
  const heats = snapshot.devices.filter((d) => d.category === "water_heater" && d.enabled);
  const list: Consumer[] = [
    ...(cars.length ? cars.map((c) => carConsumer(t, c)) : [carConsumer(t, undefined)]),
    ...(heats.length ? heats.map((h) => heatConsumer(t, h)) : [heatConsumer(t, undefined)]),
  ];
  if (list.length <= MAX_CONSUMERS) return list;
  const shown = list.slice(0, MAX_CONSUMERS - 1);
  const rest = list.slice(MAX_CONSUMERS - 1).filter((c) => !c.absent);
  const watts = rest.reduce((s, c) => s + c.watts, 0);
  shown.push({
    key: "more", watts, tone: rest[0]?.tone ?? "water",
    value: fmtW(watts), sub: t("flow.more", { n: rest.length }),
    link: "/warmwasser",
    art: rest[0]?.art ?? ((on) => <WaterArt active={on} temp={null} />),
  });
  return shown;
}

export function PowerFlow({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useI18n();
  const grid = snapshot.grid_power ?? 0;
  const bat = snapshot.battery?.power ?? 0;
  const soc = snapshot.battery?.soc ?? null;
  const pvOn = snapshot.pv_power > 20;
  const batDir: Dir = bat < -20 ? "fwd" : bat > 20 ? "rev" : "none";

  const fixed: NodeInfo[] = [
    {
      key: "pv", x: POS.pv[0], y: POS.pv[1], w: ART_W, path: PATH.pv,
      watts: snapshot.pv_power, dir: pvOn ? "fwd" : "none", tone: "pv",
      value: fmtW(snapshot.pv_power), sub: t("flow.pv"),
      art: <PvArt active={pvOn} />,
    },
    {
      key: "grid", x: POS.grid[0], y: POS.grid[1], w: ART_W, path: PATH.grid,
      watts: Math.abs(grid), dir: grid > 20 ? "fwd" : grid < -20 ? "rev" : "none",
      tone: grid < -20 ? "grid-export" : "grid-import",
      value: fmtW(Math.abs(grid)),
      sub: grid > 20 ? t("flow.importing") : grid < -20 ? t("flow.exporting") : t("flow.grid"),
      alert: snapshot.waste_w > 150, link: "/tarif",
      art: <GridArt />,
    },
    {
      key: "battery", x: POS.battery[0], y: POS.battery[1], w: ART_W, path: PATH.battery,
      watts: Math.abs(bat), dir: batDir, tone: "battery",
      value: snapshot.battery ? fmtW(Math.abs(bat)) : "–",
      sub: !snapshot.battery ? t("flow.battery")
        : bat > 20 ? t("flow.charging") : bat < -20 ? t("flow.discharging") : t("flow.idle"),
      badge: soc != null ? { icon: "battery", text: fmtPct(soc), tone: soc < 15 ? "warn" : "ok" } : undefined,
      absent: !snapshot.battery, link: "/batterie",
      art: <BatteryArt soc={soc} active={batDir !== "none"} />,
    },
  ];

  const list = consumers(t, snapshot);
  const slots = consumerSlots(list.length);
  const bottom: NodeInfo[] = list.map((c, i) => {
    const x = slots.xs[i];
    const on = c.watts > 20;
    return {
      key: c.key, x, y: ROW_Y, w: slots.w, path: consumerPath(x, ROW_Y, slots.w),
      watts: c.watts, dir: on ? "fwd" : "none", tone: c.tone, value: c.value, sub: c.sub,
      badge: c.badge, absent: c.absent, link: c.link, art: c.art(on),
    };
  });
  const nodes = [...fixed, ...bottom];
  const tones = Array.from(new Set(["pv", "grid-import", "grid-export", "battery", ...bottom.map((n) => n.tone)]));

  return (
    <div className={`pflow ${slots.w < ART_W ? "dense" : ""}`} role="img" aria-label={describe(t, snapshot, nodes)}>
      <svg className="pflow-lines" viewBox={`0 0 ${VW} ${VH}`} aria-hidden="true">
        <defs>
          {tones.map((tone) => (
            <g key={tone}>
              <marker id={`pf-arrow-${tone}`} viewBox="0 0 10 10" refX="7" refY="5" markerWidth="3.2" markerHeight="3.2"
                orient="auto-start-reverse" markerUnits="strokeWidth">
                <path d="M 1 1 L 8 5 L 1 9" fill="none" stroke={`var(--c-${tone})`} strokeWidth="2"
                  strokeLinecap="round" strokeLinejoin="round" />
              </marker>
              <marker id={`pf-dot-${tone}`} viewBox="0 0 10 10" refX="5" refY="5" markerWidth="2.4" markerHeight="2.4"
                markerUnits="strokeWidth">
                <circle cx="5" cy="5" r="4" fill={`var(--c-${tone})`} />
              </marker>
            </g>
          ))}
        </defs>
        {nodes.map((n) => {
          const on = n.dir !== "none";
          const style = { "--dur": `${duration(n.watts).toFixed(2)}s`, "--w": width(n.watts).toFixed(2) } as CSSProperties;
          return (
            <g key={n.key} className={`pflow-wire tone-${n.tone} ${on ? "active" : ""} dir-${n.dir} ${n.absent ? "absent" : ""}`}
               style={style}>
              <path className="pflow-track" d={n.path}
                markerEnd={on ? `url(#pf-${n.dir === "fwd" ? "arrow" : "dot"}-${n.tone})` : undefined}
                markerStart={on ? `url(#pf-${n.dir === "fwd" ? "dot" : "arrow"}-${n.tone})` : undefined} />
              {on && <path className="pflow-dots" d={n.path} />}
            </g>
          );
        })}
      </svg>

      {nodes.map((n) => <FlowNode key={n.key} n={n} />)}

      <div className="pflow-node tone-house" style={{ left: pct(POS.house[0], VW), top: pct(POS.house[1] - (HOUSE_W * RATIO) / 2, VH), width: pct(HOUSE_W, VW) }}>
        <span className="pflow-art"><HouseArt /></span>
        <Label value={fmtW(snapshot.house_power)} sub={t("flow.house")} />
      </div>
    </div>
  );
}

function FlowNode({ n }: { n: NodeInfo }) {
  const style = { left: pct(n.x, VW), top: pct(n.y - (n.w * RATIO) / 2, VH), width: pct(n.w, VW) };
  const cls = `pflow-node tone-${n.tone} ${n.dir !== "none" ? "active" : ""} ${n.absent ? "absent" : ""} ${n.alert ? "alert" : ""}`;
  const inner = (
    <>
      <span className="pflow-art">{n.art}</span>
      <Label value={n.value} sub={n.sub} badge={n.badge} />
    </>
  );
  return n.link && !n.absent
    ? <Link to={n.link} className={cls} style={style}>{inner}</Link>
    : <div className={cls} style={style}>{inner}</div>;
}

/** „2.432 W“ → große Zahl, kleine Einheit – wie in der Vorlage. */
function Label({ value, sub, badge }: { value: string; sub: string; badge?: Badge }) {
  const i = value.lastIndexOf(" ");
  const num = i > 0 ? value.slice(0, i) : value;
  const unit = i > 0 ? value.slice(i + 1) : "";
  return (
    <span className="pflow-label">
      <span className="pflow-value num"><b>{num}</b>{unit && <> {unit}</>}</span>
      <span className="pflow-sub">{sub}</span>
      {badge && (
        <span className={`pflow-badge ${badge.tone}`}><Icon name={badge.icon} size={13} />{badge.text}</span>
      )}
    </span>
  );
}

/** Kurzer Satz für Screenreader: Das Bild selbst sagt ihnen nichts. */
function describe(t: T, s: Snapshot, nodes: NodeInfo[]): string {
  const parts = nodes.filter((n) => !n.absent).map((n) => `${n.sub} ${n.value}`);
  return `${t("flow.title")}: ${t("flow.house")} ${fmtW(s.house_power)}, ${parts.join(", ")}`;
}
