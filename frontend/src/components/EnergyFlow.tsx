/** Energiefluss als Knotendiagramm.
 *
 *  Zurück zur vertrauten Topologie — mit einer Korrektur und einer anderen
 *  gestalterischen Handschrift.
 *
 *  **Korrektur:** Die ursprüngliche Fassung führte jede Leitung über den
 *  Knoten „Haus“ (PV→Haus, Haus→Auto, Haus→Batterie). Das Haus ist aber ein
 *  Verbraucher wie Auto und Warmwasser, keine Sammelschiene. In der Mitte
 *  steht deshalb jetzt der Netzpunkt: der Ort, auf den tatsächlich geregelt
 *  wird. Von dort gehen alle Zweige ab, das Haus ist einer davon.
 *
 *  **Gestaltung:** Keine gestrichelten Lauflichter auf gebogenen Linien –
 *  stattdessen fließen einzelne Punkte über die Bahn, deren Tempo an der
 *  Leistung hängt. Knoten sind flache Kacheln mit weichem Farbschimmer statt
 *  Kreisen mit Rand; die Leitung selbst trägt einen Farbverlauf vom Ursprung
 *  zum Ziel, sodass die Richtung auch im Standbild ablesbar bleibt.
 */
import { useId } from "react";
import { useI18n } from "../i18n";
import { fmtW } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { useAnimatedNumber, useFlashOnChange } from "./ui";
import { Icon, type IconName } from "./icons";

/** Darunter gilt ein Zweig als still. */
const ACTIVE_W = 30;

const VB = { w: 680, h: 470 };
const HUB = { x: 340, y: 235 };

const NODES = {
  pv:      { x: 340, y: 58 },
  grid:    { x: 92,  y: 150 },
  battery: { x: 588, y: 150 },
  house:   { x: 92,  y: 372 },
  car:     { x: 340, y: 412 },
  water:   { x: 588, y: 372 },
};

const NODE_W = 118;
const NODE_H = 74;

/** Anschlusspunkt am Kachelrand in Richtung Mittelpunkt – die Leitung soll an
 *  der Kachel enden, nicht unter ihr verschwinden. */
function anchor(n: { x: number; y: number }) {
  const dx = HUB.x - n.x;
  const dy = HUB.y - n.y;
  const len = Math.hypot(dx, dy) || 1;
  const hw = NODE_W / 2 + 4;
  const hh = NODE_H / 2 + 4;
  // Skalierung auf den Rand des Rechtecks (Achse mit der größeren Überdeckung)
  const s = Math.min(Math.abs(hw / (dx / len || 1e-6)), Math.abs(hh / (dy / len || 1e-6)));
  return { x: n.x + (dx / len) * s, y: n.y + (dy / len) * s };
}

type EdgeProps = {
  id: string;
  node: { x: number; y: number };
  power: number;
  /** true = Fluss läuft zum Knoten hin (Verbraucher), false = zum Netzpunkt. */
  outward: boolean;
  color: string;
  label: string;
};

function Edge({ id, node, power, outward, color, label }: EdgeProps) {
  const a = anchor(node);
  const magnitude = Math.abs(power);
  const active = magnitude > ACTIVE_W;
  // Gedämpft (Wurzel), sonst erdrückt ein einzelner großer Verbraucher das Bild.
  const width = active ? Math.min(9, 3 + Math.sqrt(magnitude) / 12) : 2.5;
  // Tempo an der Leistung, aber mit ruhigem Höchstwert: Das Diagramm läuft im
  // Dauerbetrieb auf einem Bildschirm.
  const dur = active ? Math.max(2.4, Math.min(9, 9000 / magnitude)) : 0;
  const d = outward
    ? `M ${HUB.x} ${HUB.y} L ${a.x} ${a.y}`
    : `M ${a.x} ${a.y} L ${HUB.x} ${HUB.y}`;

  return (
    <g aria-label={`${label}: ${fmtW(magnitude)}`}>
      <defs>
        <linearGradient id={`g-${id}`} gradientUnits="userSpaceOnUse"
                        x1={outward ? HUB.x : a.x} y1={outward ? HUB.y : a.y}
                        x2={outward ? a.x : HUB.x} y2={outward ? a.y : HUB.y}>
          <stop offset="0" stopColor={color} stopOpacity={active ? 0.30 : 0.14} />
          <stop offset="1" stopColor={color} stopOpacity={active ? 0.92 : 0.14} />
        </linearGradient>
        <path id={`p-${id}`} d={d} />
      </defs>
      <path d={d} stroke={`url(#g-${id})`} strokeWidth={width} strokeLinecap="round"
            fill="none" className="ef-wire" />
      {active && [0, 0.5].map((delay) => (
        <circle key={delay} r={Math.min(4.5, width / 1.9)} fill={color} className="ef-dot">
          <animateMotion dur={`${dur}s`} repeatCount="indefinite"
                         begin={`${-delay * dur}s`}>
            <mpath href={`#p-${id}`} />
          </animateMotion>
        </circle>
      ))}
    </g>
  );
}

function Node({ n, icon, label, value, sub, accent, muted = false, flash = false }: {
  n: { x: number; y: number }; icon: IconName; label: string;
  value: React.ReactNode; sub?: string; accent: string; muted?: boolean; flash?: boolean;
}) {
  const x = n.x - NODE_W / 2;
  const y = n.y - NODE_H / 2;
  return (
    <g className={flash ? "flash" : undefined}
       style={{ opacity: muted ? 0.42 : 1, transformOrigin: `${n.x}px ${n.y}px`,
                transition: "opacity var(--t-slow) var(--ease)" }}>
      {/* Farbschimmer statt Rand: hebt die Kachel ab, ohne sie einzurahmen */}
      <rect x={x} y={y} width={NODE_W} height={NODE_H} rx="18"
            fill={accent} fillOpacity={muted ? 0.05 : 0.11} />
      <rect x={x} y={y} width={NODE_W} height={NODE_H} rx="18" className="ef-node" />
      <g transform={`translate(${n.x - 9}, ${y + 11})`} color={accent}>
        <Icon name={icon} size={18} />
      </g>
      <text x={n.x} y={y + 48} textAnchor="middle" className="ef-node-value num">{value}</text>
      <text x={n.x} y={y + 64} textAnchor="middle" className="ef-node-label">
        {sub ?? label}
      </text>
    </g>
  );
}

/** In <text> ist kein <span> erlaubt – der Hook liefert deshalb nur den String. */
function FlowValue({ watts }: { watts: number }) {
  return <>{fmtW(useAnimatedNumber(watts))}</>;
}

/** Netzpunkt: Zahl und Beschriftung aus **einer** Quelle.
 *
 *  Vorher zählte der Wert weich hoch, während das Etikett sofort umsprang –
 *  auf dem Weg von 144 W nach null stand dann „144 W · Ausgeglichen“ da, was
 *  sich selbst widerspricht. Beides hängt jetzt am selben interpolierten
 *  Wert, damit die Beschriftung genau dann kippt, wenn es die Zahl auch tut. */
function Hub({ grid, t }: { grid: number; t: (k: string) => string }) {
  const shown = useAnimatedNumber(grid);
  const balanced = Math.abs(shown) <= ACTIVE_W;
  return (
    <g>
      <circle cx={HUB.x} cy={HUB.y} r="34" className="ef-hub" />
      <circle cx={HUB.x} cy={HUB.y} r="34" className={balanced ? "ef-hub-ring ok" : "ef-hub-ring"} />
      <text x={HUB.x} y={HUB.y - 2} textAnchor="middle" className="ef-hub-value num">
        {fmtW(Math.abs(shown))}
      </text>
      <text x={HUB.x} y={HUB.y + 13} textAnchor="middle" className="ef-hub-label">
        {balanced ? t("dash.balancePerfect")
          : shown > 0 ? t("dash.import") : t("dash.export")}
      </text>
    </g>
  );
}

export function EnergyFlow({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useI18n();
  const uid = useId().replace(/:/g, "");

  const grid = snapshot.grid_power ?? 0;
  const bat = snapshot.battery?.power ?? 0;
  const importing = grid > ACTIVE_W;
  const gridColor = importing ? "var(--c-grid-import)" : "var(--c-grid-export)";

  const hasBattery = snapshot.battery != null;
  const hasCar = snapshot.devices.some((d) => d.category === "wallbox" && d.enabled);
  const hasWater = snapshot.devices.some((d) => d.category === "water_heater" && d.enabled);

  const carFlash = useFlashOnChange(snapshot.wallbox_power > ACTIVE_W);
  const waterFlash = useFlashOnChange(snapshot.water_power > ACTIVE_W);
  const gridFlash = useFlashOnChange(importing);
  const batFlash = useFlashOnChange(bat > ACTIVE_W ? "charge" : bat < -ACTIVE_W ? "discharge" : "idle");

  const batterySub = !hasBattery ? undefined
    : bat > ACTIVE_W ? `${t("dash.charging")} ${fmtW(bat)}`
    : bat < -ACTIVE_W ? `${t("dash.discharging")} ${fmtW(Math.abs(bat))}`
    : undefined;


  return (
    <svg viewBox={`0 0 ${VB.w} ${VB.h}`} className="ef"
         style={{ width: "100%", height: "auto", display: "block" }}
         role="img" aria-label={t("dash.energyFlow")}>
      {/* Erzeuger und Speicher speisen ein, Verbraucher zapfen ab. */}
      <Edge id={`pv${uid}`} node={NODES.pv} power={snapshot.pv_power}
            outward={false} color="var(--c-pv)" label={t("dash.pv")} />
      <Edge id={`gr${uid}`} node={NODES.grid} power={grid}
            outward={grid < 0} color={gridColor} label={t("dash.grid")} />
      <Edge id={`ba${uid}`} node={NODES.battery} power={bat}
            outward={bat > 0} color="var(--c-battery)" label={t("dash.battery")} />
      <Edge id={`ho${uid}`} node={NODES.house} power={snapshot.house_power}
            outward color="var(--c-house)" label={t("dash.house")} />
      <Edge id={`ca${uid}`} node={NODES.car} power={snapshot.wallbox_power}
            outward color="var(--c-car)" label={t("dash.car")} />
      <Edge id={`wa${uid}`} node={NODES.water} power={snapshot.water_power}
            outward color="var(--c-water)" label={t("dash.water")} />

      {/* Netzpunkt in der Mitte – das, worauf geregelt wird */}
      <Hub grid={grid} t={t} />

      <Node n={NODES.pv} icon="sun" label={t("dash.pv")} accent="var(--c-pv)"
            value={<FlowValue watts={snapshot.pv_power} />} sub={t("dash.pv")} />
      <Node n={NODES.grid} icon="grid" label={t("dash.grid")} accent={gridColor}
            value={<FlowValue watts={Math.abs(grid)} />} flash={gridFlash}
            sub={importing ? t("dash.import") : grid < -ACTIVE_W ? t("dash.export") : t("dash.grid")} />
      <Node n={NODES.battery} icon="battery" label={t("dash.battery")} accent="var(--c-battery)"
            value={hasBattery ? `${Math.round(snapshot.battery!.soc)} %` : "–"}
            sub={batterySub ?? t("dash.battery")} muted={!hasBattery} flash={batFlash} />
      <Node n={NODES.house} icon="house" label={t("dash.house")} accent="var(--c-house)"
            value={<FlowValue watts={snapshot.house_power} />} sub={t("dash.house")} />
      <Node n={NODES.car} icon="car" label={t("dash.car")} accent="var(--c-car)"
            value={<FlowValue watts={snapshot.wallbox_power} />} sub={t("dash.car")}
            muted={!hasCar} flash={carFlash} />
      <Node n={NODES.water} icon="water" label={t("dash.water")} accent="var(--c-water)"
            value={<FlowValue watts={snapshot.water_power} />} sub={t("dash.water")}
            muted={!hasWater} flash={waterFlash} />
    </svg>
  );
}
