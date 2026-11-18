/** Energiefluss: Woher kommt der Strom, wohin geht er?
 *
 *  Aufbau wie in den Hersteller-Apps: Jedes Gerät steht als isometrisches
 *  Bild auf einer Kachel. Oben die Sonne, links das Netz, rechts der
 *  Speicher, in der Mitte das Haus – und unten zusätzlich Auto und
 *  Warmwasser, die das Haus versorgt.
 *
 *  Die Leitungen sind dynamisch:
 *   - sie erscheinen farbig nur, wenn wirklich Strom fließt,
 *   - Pfeil und laufende Punkte zeigen die Richtung (Einspeisen, Laden …),
 *   - je mehr Leistung, desto dicker die Leitung und desto schneller die Punkte.
 *  Ohne Bewegungswunsch (prefers-reduced-motion) stehen die Punkte still,
 *  die Richtung zeigt dann der Pfeil.
 *
 *  Bilder und Leitungen liegen in einem gemeinsamen Koordinatensystem
 *  (400 × 470) mit festem Seitenverhältnis; die Zahlen darüber sind HTML,
 *  damit Schrift auf jedem Bildschirm gleich groß und scharf bleibt.
 */
import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";
import { useI18n, type T } from "../i18n";
import { fmtPct, fmtTemp, fmtW } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { BatteryArt, CarArt, GridArt, HouseArt, PvArt, WaterArt } from "./FlowArt";
import { Icon, type IconName } from "./icons";

type Key = "pv" | "grid" | "battery" | "car" | "water";
type Dir = "fwd" | "rev" | "none";

const VW = 400;
const VH = 510;
const ART_W = 124;               // Bildbreite in Diagramm-Einheiten
const HOUSE_W = 144;
const ART_H = ART_W * (100 / 120);

/** Bildmitte jedes Knotens. */
const POS: Record<Key | "house", [number, number]> = {
  pv: [200, 56],
  grid: [68, 154],
  battery: [332, 154],
  house: [200, 284],
  car: [68, 396],
  water: [332, 396],
};

/** Leitungen in „natürlicher“ Richtung: Quellen → Haus, Haus → Verbraucher.
 *  Fließt Strom andersherum (Einspeisen, Speicher laden), läuft die
 *  Animation rückwärts. */
const PATH: Record<Key, string> = {
  pv: "M 200 160 V 238",
  grid: "M 126 218 H 164 Q 182 218 182 236",
  battery: "M 274 218 H 236 Q 218 218 218 236",
  car: "M 150 322 H 96 Q 68 322 68 350 V 362",
  water: "M 250 322 H 304 Q 332 322 332 350 V 362",
};

const LINK: Partial<Record<Key | "house", string>> = {
  car: "/auto", water: "/warmwasser", battery: "/batterie", grid: "/tarif",
};

/** Animationsdauer aus Leistung: 1 kW ≈ 2,4 s je Durchlauf, 10 kW ≈ 0,7 s. */
const duration = (w: number) => Math.max(0.6, Math.min(3.2, 3.2 - Math.log10(Math.max(w, 50) / 50) * 1.15));

/** Leitungsstärke aus Leistung: 2 (kaum) bis 5,5 (≥ 11 kW). */
const width = (w: number) => Math.max(2, Math.min(5.5, 2 + Math.log10(Math.max(w, 50) / 50) * 1.5));

type Badge = { icon: IconName; text: string; tone: "ok" | "warn" | "info" };

type NodeInfo = {
  key: Key;
  watts: number;
  dir: Dir;
  tone: string;
  value: string;
  sub: string;
  badge?: Badge;
  absent?: boolean;
  alert?: boolean;
  art: ReactNode;
};

const pct = (v: number, of: number) => `${(v / of) * 100}%`;

export function PowerFlow({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useI18n();
  const grid = snapshot.grid_power ?? 0;
  const bat = snapshot.battery?.power ?? 0;
  const car = snapshot.devices.find((d) => d.category === "wallbox" && d.enabled);
  const water = snapshot.devices.find((d) => d.category === "water_heater" && d.enabled);
  const waterTemp = typeof water?.data?.temperature_c === "number" ? (water.data.temperature_c as number) : null;
  const carSoc = typeof car?.data?.soc === "number" ? (car.data.soc as number) : null;
  const carPlugged = car ? (car.data?.state ?? "idle") !== "idle" : false;
  const soc = snapshot.battery?.soc ?? null;

  const pvOn = snapshot.pv_power > 20;
  const carOn = snapshot.wallbox_power > 20;
  const waterOn = snapshot.water_power > 20;
  const batDir: Dir = bat < -20 ? "fwd" : bat > 20 ? "rev" : "none";

  const nodes: NodeInfo[] = [
    {
      key: "pv", watts: snapshot.pv_power, dir: pvOn ? "fwd" : "none", tone: "pv",
      value: fmtW(snapshot.pv_power), sub: t("flow.pv"),
      art: <PvArt active={pvOn} />,
    },
    {
      key: "grid", watts: Math.abs(grid), dir: grid > 20 ? "fwd" : grid < -20 ? "rev" : "none",
      tone: grid < -20 ? "grid-export" : "grid-import",
      value: fmtW(Math.abs(grid)),
      sub: grid > 20 ? t("flow.importing") : grid < -20 ? t("flow.exporting") : t("flow.grid"),
      alert: snapshot.waste_w > 150,
      art: <GridArt />,
    },
    {
      key: "battery", watts: Math.abs(bat), dir: batDir, tone: "battery",
      value: snapshot.battery ? fmtW(Math.abs(bat)) : "–",
      sub: !snapshot.battery ? t("flow.battery")
        : bat > 20 ? t("flow.charging") : bat < -20 ? t("flow.discharging") : t("flow.idle"),
      badge: soc != null ? { icon: "battery", text: fmtPct(soc), tone: soc < 15 ? "warn" : "ok" } : undefined,
      absent: !snapshot.battery,
      art: <BatteryArt soc={soc} active={batDir !== "none"} />,
    },
    {
      key: "car", watts: snapshot.wallbox_power, dir: carOn ? "fwd" : "none", tone: "car",
      value: car ? fmtW(snapshot.wallbox_power) : "–",
      sub: car?.name && car.name.length <= 18 ? car.name : t("flow.car"),
      badge: carSoc != null ? { icon: "car", text: fmtPct(carSoc), tone: "ok" } : undefined,
      absent: !car,
      art: <CarArt active={carOn} present={!car || carPlugged || carOn} />,
    },
    {
      key: "water", watts: snapshot.water_power, dir: waterOn ? "fwd" : "none", tone: "water",
      value: water ? fmtW(snapshot.water_power) : "–",
      sub: t("flow.water"),
      badge: waterTemp != null ? { icon: "thermo", text: fmtTemp(waterTemp), tone: "info" } : undefined,
      absent: !water,
      art: <WaterArt active={waterOn} temp={waterTemp} />,
    },
  ];

  const tones = ["pv", "grid-import", "grid-export", "battery", "car", "water"];

  return (
    <div className="pflow" role="img" aria-label={describe(t, snapshot, nodes)}>
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
              <path className="pflow-track" d={PATH[n.key]}
                markerEnd={on ? `url(#pf-${n.dir === "fwd" ? "arrow" : "dot"}-${n.tone})` : undefined}
                markerStart={on ? `url(#pf-${n.dir === "fwd" ? "dot" : "arrow"}-${n.tone})` : undefined} />
              {on && <path className="pflow-dots" d={PATH[n.key]} />}
            </g>
          );
        })}
      </svg>

      {nodes.map((n) => <FlowNode key={n.key} n={n} />)}

      <div className="pflow-node tone-house" style={{ left: pct(POS.house[0], VW), top: pct(POS.house[1] - (HOUSE_W * 100 / 120) / 2, VH), width: pct(HOUSE_W, VW) }}>
        <span className="pflow-art"><HouseArt /></span>
        <Label value={fmtW(snapshot.house_power)} sub={t("flow.house")} />
      </div>
    </div>
  );
}

function FlowNode({ n }: { n: NodeInfo }) {
  const [x, y] = POS[n.key];
  const style = { left: pct(x, VW), top: pct(y - ART_H / 2, VH), width: pct(ART_W, VW) };
  const cls = `pflow-node tone-${n.tone} ${n.dir !== "none" ? "active" : ""} ${n.absent ? "absent" : ""} ${n.alert ? "alert" : ""}`;
  const inner = (
    <>
      <span className="pflow-art">{n.art}</span>
      <Label value={n.value} sub={n.sub} badge={n.badge} />
    </>
  );
  const to = LINK[n.key];
  return to && !n.absent
    ? <Link to={to} className={cls} style={style}>{inner}</Link>
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
