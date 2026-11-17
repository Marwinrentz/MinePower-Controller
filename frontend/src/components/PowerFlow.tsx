/** Energiefluss: Woher kommt der Strom, wohin geht er?
 *
 *  Sechs Knoten um das Haus in der Mitte – Sonne und Netz oben, Batterie,
 *  Auto und Warmwasser unten. Leitungen leuchten nur, wenn Strom fließt, und
 *  die Punkte darauf laufen in Flussrichtung; je mehr Leistung, desto
 *  schneller. Ohne Bewegungswunsch (prefers-reduced-motion) stehen sie still,
 *  die Richtung zeigt dann der Pfeil am Wert.
 *
 *  Gebaut aus HTML-Knoten über einer SVG-Leitungsebene statt als ein einziges
 *  SVG: Text bleibt auf jedem Bildschirm gleich groß und scharf, nur die
 *  Abstände wachsen mit. Das alte Diagramm war ein 680 px breites SVG – am
 *  Handy wurde die Schrift darin winzig.
 */
import { Suspense, lazy } from "react";
import { Link } from "react-router-dom";
import { useI18n } from "../i18n";
import { fmtPct, fmtTemp, fmtW } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { useBetaUi } from "../lib/variant";
import { Icon, type IconName } from "./icons";

// Neue Grafik der BETA-Oberfläche – dieselben Knoten, andere Zeichnung.
const FlowScene = lazy(() => import("../beta/FlowScene"));

export type FlowKey = "pv" | "grid" | "battery" | "car" | "water";
type Key = FlowKey;

/** Spalte, Zeile im 3×3-Raster (1-basiert). */
const POS: Record<Key | "house", [number, number]> = {
  pv: [1, 1], grid: [3, 1], house: [2, 2], battery: [1, 3], car: [2, 3], water: [3, 3],
};

const TONE: Record<Key, string> = {
  pv: "pv", grid: "grid", battery: "battery", car: "car", water: "water",
};

export const FLOW_LINK: Partial<Record<Key, string>> = {
  car: "/auto", water: "/warmwasser", battery: "/batterie", grid: "/tarif",
};

export const FLOW_ICON: Record<Key | "house", IconName> = {
  pv: "sun", grid: "grid", battery: "battery", car: "car", water: "water", house: "house",
};

const center = (k: Key | "house") => {
  const [c, r] = POS[k];
  return { x: (c - 0.5) * 100, y: (r - 0.5) * 100 };
};

/** Animationsdauer aus Leistung: 1 kW ≈ 2,4 s je Durchlauf, 10 kW ≈ 0,7 s. */
export const flowDuration = (w: number) => `${Math.max(0.6, Math.min(3.2, 3.2 - Math.log10(Math.max(w, 50) / 50) * 1.15)).toFixed(2)}s`;

export type FlowNode = {
  key: Key; watts: number; dir: "in" | "out" | "none"; value: string; sub: string; absent?: boolean; alert?: boolean;
  /** Füllstand in Prozent, wo es einen gibt (Batterie, Auto) – sonst null */
  level?: number | null;
};

type Translate = (key: string, vars?: Record<string, string | number>) => string;

/** Die Knoten um das Haus – Werte, Richtung, Beschriftung. Beide
 *  Darstellungen (bisherige und BETA) zeichnen aus derselben Liste. */
export function flowNodes(snapshot: Snapshot, t: Translate): FlowNode[] {
  const grid = snapshot.grid_power ?? 0;
  const bat = snapshot.battery?.power ?? 0;
  const car = snapshot.devices.find((d) => d.category === "wallbox" && d.enabled);
  const water = snapshot.devices.find((d) => d.category === "water_heater" && d.enabled);
  const waterTemp = typeof water?.data?.temperature_c === "number" ? (water.data.temperature_c as number) : null;
  const carSoc = typeof car?.data?.soc === "number" ? (car.data.soc as number) : null;

  return [
    {
      key: "pv", watts: snapshot.pv_power, dir: snapshot.pv_power > 20 ? "in" : "none",
      value: fmtW(snapshot.pv_power), sub: t("flow.pv"),
    },
    {
      key: "grid", watts: Math.abs(grid), dir: grid > 20 ? "in" : grid < -20 ? "out" : "none",
      value: fmtW(Math.abs(grid)),
      sub: grid > 20 ? t("flow.importing") : grid < -20 ? t("flow.exporting") : t("flow.grid"),
      alert: snapshot.waste_w > 150,
    },
    {
      key: "battery", watts: Math.abs(bat), dir: bat > 20 ? "out" : bat < -20 ? "in" : "none",
      value: snapshot.battery ? fmtPct(snapshot.battery.soc) : "–",
      sub: !snapshot.battery ? t("flow.battery")
        : bat > 20 ? `${t("flow.charging")} · ${fmtW(bat)}`
        : bat < -20 ? `${t("flow.discharging")} · ${fmtW(-bat)}` : t("flow.idle"),
      absent: !snapshot.battery, level: snapshot.battery ? snapshot.battery.soc : null,
    },
    {
      key: "car", watts: snapshot.wallbox_power, dir: snapshot.wallbox_power > 20 ? "out" : "none",
      value: car ? fmtW(snapshot.wallbox_power) : "–",
      sub: car ? (carSoc != null ? `${t("flow.car")} · ${fmtPct(carSoc)}` : t("flow.car")) : t("flow.car"),
      absent: !car, level: carSoc,
    },
    {
      key: "water", watts: snapshot.water_power, dir: snapshot.water_power > 20 ? "out" : "none",
      value: water ? fmtW(snapshot.water_power) : "–",
      sub: water ? (waterTemp != null ? `${t("flow.water")} · ${fmtTemp(waterTemp)}` : t("flow.water")) : t("flow.water"),
      absent: !water,
    },
  ];
}

export function PowerFlow({ snapshot }: { snapshot: Snapshot }) {
  const beta = useBetaUi();
  if (!beta) return <ClassicPowerFlow snapshot={snapshot} />;
  return (
    <Suspense fallback={<div className="pflow" aria-busy="true" />}>
      <FlowScene snapshot={snapshot} />
    </Suspense>
  );
}

function ClassicPowerFlow({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useI18n();
  const nodes = flowNodes(snapshot, t);
  const hub = center("house");

  return (
    <div className="pflow" role="img" aria-label={t("flow.title")}>
      <svg className="pflow-lines" viewBox="0 0 300 300" preserveAspectRatio="none" aria-hidden="true">
        {nodes.map((n) => {
          const c = center(n.key);
          // Pfad immer vom Knoten zum Haus; 'out" läuft rückwärts.
          const d = `M ${c.x} ${c.y} L ${hub.x} ${hub.y}`;
          return (
            <g key={n.key} className={`pflow-wire tone-${TONE[n.key]} ${n.dir !== "none" ? "active" : ""} dir-${n.dir}`}
               style={{ ["--dur" as string]: flowDuration(n.watts) }}>
              <path className="pflow-track" d={d} vectorEffect="non-scaling-stroke" />
              {n.dir !== "none" && <path className="pflow-dots" d={d} vectorEffect="non-scaling-stroke" />}
            </g>
          );
        })}
      </svg>

      {nodes.map((n) => {
        const [c, r] = POS[n.key];
        const inner = (
          <>
            <span className="pflow-icon"><Icon name={FLOW_ICON[n.key]} size={20} /></span>
            <span className="pflow-value num">{n.value}</span>
            <SubLine text={n.sub} />
          </>
        );
        const cls = `pflow-node tone-${TONE[n.key]} ${n.dir !== "none" ? "active" : ""} ${n.absent ? "absent" : ""} ${n.alert ? "alert" : ""}`;
        const style = { left: `${((c - 0.5) / 3) * 100}%`, top: `${((r - 0.5) / 3) * 100}%` };
        const to = FLOW_LINK[n.key];
        return to && !n.absent
          ? <Link key={n.key} to={to} className={cls} style={style}>{inner}</Link>
          : <div key={n.key} className={cls} style={style}>{inner}</div>;
      })}

      <div className="pflow-hub" style={{ left: "50%", top: "50%" }}>
        <span className="pflow-icon"><Icon name="house" size={22} /></span>
        <span className="pflow-value num">{fmtW(snapshot.house_power)}</span>
        <span className="pflow-sub">{t("flow.house")}</span>
      </div>
    </div>
  );
}

/** Unterzeile eines Knotens. 'Warmwasser · 48 °C" steht auf breiten
 *  Bildschirmen in einer Zeile, auf schmalen Handys in zwei – statt mit '…"
 *  abgeschnitten zu werden. */
function SubLine({ text }: { text: string }) {
  const [head, ...rest] = text.split(" · ");
  return (
    <span className="pflow-sub">
      {head}
      {rest.length > 0 && <span className="pflow-sub2">{rest.join(" · ")}</span>}
    </span>
  );
}
