/** Energiefluss der BETA-Oberfläche.
 *
 *  Dieselben Knoten wie in der bisherigen Darstellung (components/PowerFlow),
 *  neu gezeichnet: runde Knoten mit Füllstandsring, geschwungene Leitungen,
 *  das Haus in der Mitte mit einem Ring, der den Strommix im Augenblick zeigt.
 *  Eine Leitung leuchtet und bewegt sich nur, wenn dort tatsächlich Leistung
 *  fließt; die Richtung steht zusätzlich als Wort am Knoten (Bezug,
 *  Einspeisung, lädt, entlädt). Geräte, die es in der Anlage nicht gibt,
 *  werden nicht gezeichnet.
 *
 *  Wie im Original liegen HTML-Knoten über einer SVG-Leitungsebene: Text
 *  bleibt auf jedem Bildschirm gleich groß und scharf.
 */
import { Link } from "react-router-dom";
import { Icon } from "../components/icons";
import { FLOW_ICON, FLOW_LINK, flowDuration, flowNodes, type FlowKey, type FlowNode } from "../components/PowerFlow";
import { useI18n } from "../i18n";
import { fmtPct, fmtW } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { MIX_KEYS, supplyMix, type Mix } from "./mix";

/** Mittelpunkt je Knoten in Prozent der Fläche (x, y). */
const POS: Record<FlowKey | "house", [number, number]> = {
  pv: [17, 21], grid: [83, 21], house: [50, 44], battery: [17, 76], car: [50, 83], water: [83, 76],
};

/** Leitung vom Knoten zum Haus (Koordinaten wie POS). */
const WIRE: Record<FlowKey, string> = {
  pv: "M17 21 C17 40 33 43 50 44",
  grid: "M83 21 C83 40 67 43 50 44",
  battery: "M17 76 C17 58 33 47 50 45",
  water: "M83 76 C83 58 67 47 50 45",
  car: "M50 83 L50 44",
};

const toneOf = (n: FlowNode) => (n.key === "grid" ? (n.dir === "out" ? "export" : "import") : n.key);

function MixRing({ mix }: { mix: Mix | null }) {
  const parts = mix ? MIX_KEYS.map((key) => ({ key, share: mix[key] * 100 })).filter((p) => p.share > 0.5) : [];
  const gap = parts.length > 1 ? 1.6 : 0;
  let start = 0;
  return (
    <svg className="fx-ring" viewBox="0 0 100 100" aria-hidden="true">
      <circle className="fx-ring-track" cx="50" cy="50" r="46" />
      {parts.map((p) => {
        const length = Math.max(0.6, p.share - gap);
        const offset = -start;
        start += p.share;
        return (
          <circle key={p.key} className={`fx-ring-part tone-${p.key}`} cx="50" cy="50" r="46" pathLength={100}
                  strokeDasharray={`${length} ${100 - length}`} strokeDashoffset={offset} />
        );
      })}
    </svg>
  );
}

function Node({ node }: { node: FlowNode }) {
  const [x, y] = POS[node.key];
  const active = node.dir !== "none";
  const level = node.level == null ? null : Math.max(0, Math.min(100, node.level));
  const cls = `fx-node tone-${toneOf(node)} ${y < 50 ? "top" : "bottom"} ${active ? "active" : ""} ${node.alert ? "alert" : ""}`;
  const style = { left: `${x}%`, top: `${y}%`, ["--level" as string]: level ?? 0 };
  const [head, ...rest] = node.sub.split(" · ");
  const inner = (
    <>
      <span className={`fx-orb ${level != null ? "has-level" : ""}`}>
        <span className="fx-orb-face"><Icon name={FLOW_ICON[node.key]} size={24} /></span>
      </span>
      <span className="fx-text">
        <span className="fx-value num">{node.value}</span>
        <span className="fx-sub">{head}{rest.length > 0 && <span className="fx-sub2 num">{rest.join(" · ")}</span>}</span>
      </span>
    </>
  );
  const to = FLOW_LINK[node.key];
  return to
    ? <Link to={to} className={cls} style={style}>{inner}</Link>
    : <div className={cls} style={style}>{inner}</div>;
}

export default function FlowScene({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useI18n();
  const nodes = flowNodes(snapshot, t).filter((n) => !n.absent);
  const mix = supplyMix(snapshot);
  const [hx, hy] = POS.house;

  return (
    <div className="fx">
      <div className="fx-stage" role="group" aria-label={t("flow.title")}>
        <svg className="fx-wires" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {nodes.map((n) => {
            const active = n.dir !== "none";
            return (
              <g key={n.key} className={`fx-wire tone-${toneOf(n)} dir-${n.dir} ${active ? "active" : ""}`}
                 style={{ ["--dur" as string]: flowDuration(n.watts) }}>
                <path className="fx-track" d={WIRE[n.key]} vectorEffect="non-scaling-stroke" />
                {active && <path className="fx-beam" d={WIRE[n.key]} vectorEffect="non-scaling-stroke" />}
                {active && <path className="fx-dots" d={WIRE[n.key]} vectorEffect="non-scaling-stroke" />}
              </g>
            );
          })}
        </svg>

        {nodes.map((n) => <Node key={n.key} node={n} />)}

        <div className="fx-hub" style={{ left: `${hx}%`, top: `${hy}%` }}>
          <MixRing mix={mix} />
          <span className="fx-hub-icon"><Icon name="house" size={22} /></span>
          <span className="fx-hub-value num">{fmtW(snapshot.house_power)}</span>
          <span className="fx-hub-label">{t("flow.house")}</span>
        </div>
      </div>

      <div className="fx-mix">
        <div className="fx-mix-head">
          <span className="fx-mix-title">{t("beta.mix.title")}</span>
          {mix && (
            <span className="fx-mix-self">
              {t("beta.mix.self")} <strong className="num">{fmtPct((1 - mix.grid) * 100)}</strong>
            </span>
          )}
        </div>
        {mix ? (
          <>
            <div className="fx-mix-bar" aria-hidden="true">
              {MIX_KEYS.filter((key) => mix[key] > 0.005).map((key) => (
                <span key={key} className={`tone-${key}`} style={{ flexGrow: mix[key] }} />
              ))}
            </div>
            <ul className="fx-mix-legend">
              {MIX_KEYS.map((key) => (
                <li key={key} className={`tone-${key}`}>
                  <i aria-hidden="true" />
                  <span>{t(`beta.mix.${key}`)}</span>
                  <strong className="num">{fmtPct(mix[key] * 100)}</strong>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="fx-mix-none">{t("beta.mix.none")}</p>
        )}
      </div>
    </div>
  );
}
