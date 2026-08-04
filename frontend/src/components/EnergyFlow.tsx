/** Energiefluss-Diagramm – das Herzstück des Dashboards.
 *
 *  Gestaltungsregeln:
 *  • Jede Linie zeigt Richtung, Stärke und Tempo der tatsächlichen Leistung.
 *    Dicke ∝ Leistung, Tempo ∝ Leistung – man *sieht*, wo viel fließt.
 *  • Zahlen zählen weich hoch statt zu springen (siehe AnimatedValue).
 *  • Inaktive Pfade verschwinden nicht, sie werden nur still. So bleibt die
 *    Anlagentopologie stabil lesbar, statt bei jedem Tick umzuspringen.
 *  • Ein Knoten ohne Gerät (keine Batterie, kein Warmwasser) wird gedimmt
 *    dargestellt – kein Platzhalter-Rauschen, aber auch kein Layout-Sprung.
 */
import { useI18n } from "../i18n";
import { fmtW } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { useAnimatedNumber, useFlashOnChange } from "./ui";
import { Icon, type IconName } from "./icons";

const NODES = {
  pv: { x: 330, y: 62 },
  house: { x: 330, y: 236 },
  grid: { x: 92, y: 236 },
  battery: { x: 568, y: 236 },
  car: { x: 186, y: 404 },
  water: { x: 474, y: 404 },
};

const ACTIVE_W = 30; // darunter gilt eine Verbindung als still

type EdgeProps = {
  from: { x: number; y: number };
  to: { x: number; y: number };
  power: number;
  reverse?: boolean;
  color: string;
  curve?: number;
  label: string;
};

function Edge({ from, to, power, reverse = false, color, curve = 0, label }: EdgeProps) {
  const mx = (from.x + to.x) / 2 + curve;
  const my = (from.y + to.y) / 2 + Math.abs(curve) * 0.3;
  const d = `M ${from.x} ${from.y} Q ${mx} ${my} ${to.x} ${to.y}`;
  const magnitude = Math.abs(power);
  const active = magnitude > ACTIVE_W;
  // Dicke wächst gedämpft (Wurzel), sonst dominiert ein einzelner großer
  // Verbraucher das ganze Bild.
  const width = active ? Math.min(8, 2.2 + Math.sqrt(magnitude) / 13) : 2;
  // Tempo: viel Leistung = schnellerer Fluss – aber mit einem ruhigen
  // Höchsttempo (7 s je Dash-Zyklus). Das Diagramm läuft im Dauerbetrieb auf
  // einem Bildschirm; alles Schnellere zieht dauernd den Blick auf sich.
  const duration = active ? Math.max(7.0, Math.min(30, 34000 / magnitude)) : 0;

  return (
    <g aria-label={`${label}: ${fmtW(magnitude)}`}>
      <path className="flow-line" d={d} stroke="var(--surface-3)" strokeWidth={width + 3} />
      {active && (
        <path
          className="flow-line flow-anim"
          d={d}
          stroke={color}
          strokeWidth={width}
          strokeDasharray="1 16"
          style={{
            animationDuration: `${duration}s`,
            animationDirection: reverse ? "reverse" : "normal",
            transition: "stroke-width var(--t-slow) var(--ease)",
          }}
        />
      )}
    </g>
  );
}

function Node({ x, y, icon, label, value, sub, accent, muted = false, highlight = false }: {
  x: number; y: number; icon: IconName; label: string; value: React.ReactNode;
  sub?: string; accent: string; muted?: boolean; highlight?: boolean;
}) {
  return (
    <g
      className={highlight ? "flash" : undefined}
      style={{
        opacity: muted ? 0.4 : 1,
        transformOrigin: `${x}px ${y}px`,
        transition: "opacity var(--t-slow) var(--ease)",
      }}
    >
      <circle cx={x} cy={y} r="43" fill="var(--surface)" stroke="var(--line)" strokeWidth="1" />
      <circle
        cx={x} cy={y} r="43" fill="none" stroke={accent}
        strokeOpacity={muted ? 0.2 : 0.55} strokeWidth="1.5"
        style={{ transition: "stroke-opacity var(--t-slow) var(--ease)" }}
      />
      <g transform={`translate(${x - 10}, ${y - 28})`} color={accent}>
        <Icon name={icon} size={20} />
      </g>
      <text x={x} y={y + 5} textAnchor="middle" className="flow-node-value num" fill="var(--text-strong)">
        {value}
      </text>
      <text x={x} y={y + 20} textAnchor="middle" className="flow-node-label" fill="var(--text-dim)">
        {label}
      </text>
      {sub && (
        <text x={x} y={y + 33} textAnchor="middle" className="flow-node-sub" fill={accent}>
          {sub}
        </text>
      )}
    </g>
  );
}

/** Innerhalb von <text> ist kein <span> erlaubt – diese Variante liefert
 *  deshalb nur den fertigen String als Textknoten, während die Interpolation
 *  im Hook läuft. */
function FlowValue({ watts }: { watts: number }) {
  const animated = useAnimatedNumber(watts);
  return <>{fmtW(animated)}</>;
}

export function EnergyFlow({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useI18n();
  const grid = snapshot.grid_power ?? 0;
  const bat = snapshot.battery?.power ?? 0;
  const importing = grid > ACTIVE_W;
  const gridColor = importing ? "var(--c-grid-import)" : "var(--c-grid-export)";

  const hasBattery = snapshot.battery != null;
  const hasCar = snapshot.devices.some((d) => d.category === "wallbox" && d.enabled);
  const hasWater = snapshot.devices.some((d) => d.category === "water_heater" && d.enabled);

  // Zustandswechsel kurz hervorheben: Ladung startet/stoppt, Richtung am Netz
  // kippt, Batterie wechselt zwischen Laden und Entladen.
  const carFlash = useFlashOnChange(snapshot.wallbox_power > ACTIVE_W);
  const waterFlash = useFlashOnChange(snapshot.water_power > ACTIVE_W);
  const gridFlash = useFlashOnChange(importing);
  const batFlash = useFlashOnChange(bat > ACTIVE_W ? "charge" : bat < -ACTIVE_W ? "discharge" : "idle");

  const batterySub = !hasBattery
    ? undefined
    : bat > ACTIVE_W
      ? `${t("dash.charging")} ${fmtW(bat)}`
      : bat < -ACTIVE_W
        ? `${t("dash.discharging")} ${fmtW(Math.abs(bat))}`
        : undefined;

  return (
    <svg
      viewBox="0 0 660 470"
      style={{ width: "100%", height: "auto", display: "block" }}
      role="img"
      aria-label={t("dash.energyFlow")}
    >
      {/* Kanten: definiert in „positiver“ Flussrichtung */}
      <Edge from={NODES.pv} to={NODES.house} power={snapshot.pv_power}
            color="var(--c-pv)" label={t("dash.pv")} />
      <Edge from={NODES.grid} to={NODES.house} power={grid} reverse={grid < 0}
            color={gridColor} label={t("dash.grid")} />
      <Edge from={NODES.house} to={NODES.battery} power={bat} reverse={bat < 0}
            color="var(--c-battery)" label={t("dash.battery")} />
      <Edge from={NODES.house} to={NODES.car} power={snapshot.wallbox_power}
            color="var(--c-car)" curve={-30} label={t("dash.car")} />
      <Edge from={NODES.house} to={NODES.water} power={snapshot.water_power}
            color="var(--c-water)" curve={30} label={t("dash.water")} />

      <Node x={NODES.pv.x} y={NODES.pv.y} icon="sun" label={t("dash.pv")}
            value={<FlowValue watts={snapshot.pv_power} />} accent="var(--c-pv)" />
      <Node x={NODES.grid.x} y={NODES.grid.y} icon="grid" label={t("dash.grid")}
            value={<FlowValue watts={Math.abs(grid)} />}
            sub={importing ? t("dash.import") : grid < -ACTIVE_W ? t("dash.export") : undefined}
            accent={gridColor} highlight={gridFlash} />
      <Node x={NODES.house.x} y={NODES.house.y} icon="house" label={t("dash.house")}
            value={<FlowValue watts={snapshot.house_power} />} accent="var(--c-house)" />
      <Node x={NODES.battery.x} y={NODES.battery.y} icon="battery" label={t("dash.battery")}
            value={hasBattery ? `${Math.round(snapshot.battery!.soc)} %` : "–"}
            sub={batterySub} accent="var(--c-battery)"
            muted={!hasBattery} highlight={batFlash} />
      <Node x={NODES.car.x} y={NODES.car.y} icon="car" label={t("dash.car")}
            value={<FlowValue watts={snapshot.wallbox_power} />} accent="var(--c-car)"
            muted={!hasCar} highlight={carFlash} />
      <Node x={NODES.water.x} y={NODES.water.y} icon="water" label={t("dash.water")}
            value={<FlowValue watts={snapshot.water_power} />} accent="var(--c-water)"
            muted={!hasWater} highlight={waterFlash} />
    </svg>
  );
}
