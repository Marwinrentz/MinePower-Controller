/** Energiefluss als proportionales Banddiagramm.
 *
 *  Warum nicht mehr Kreise mit Verbindungslinien: Die alte Darstellung führte
 *  jede Leitung über den Knoten „Haus“ — PV→Haus, Haus→Auto, Haus→Batterie.
 *  Das Haus ist aber ein Verbraucher, keine Sammelschiene. Wer das Bild las,
 *  bekam eine Topologie gezeigt, die es so nicht gibt, und die Linienstärke
 *  sagte über die Verhältnisse fast nichts aus.
 *
 *  Hier trägt stattdessen die Fläche die Information:
 *
 *    • Bandhöhe ∝ Leistung. Zwei Bänder nebeneinander lassen sich direkt
 *      vergleichen, ohne eine einzige Zahl zu lesen.
 *    • Links steht, was einspeist, rechts, was abnimmt, in der Mitte die
 *      Schiene. Beide Seiten sind gleich hoch — Energieerhaltung wird
 *      dadurch sichtbar statt behauptet.
 *    • Was gerade nicht fließt, verschwindet. Das Bild zeigt den Zustand der
 *      Anlage, nicht ihren Bestückungsplan; Geräteliste steht daneben.
 *
 *  Bewegung: ein langsamer Schimmer entlang der Bänder. Kein Lauflicht aus
 *  Strichen — das zieht im Dauerbetrieb auf einem Wandbildschirm ständig den
 *  Blick an, ohne je etwas Neues zu sagen.
 */
import { useId } from "react";
import { useI18n } from "../i18n";
import { fmtW } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { useAnimatedNumber } from "./ui";
import { Icon, type IconName } from "./icons";

/** Darunter gilt ein Zweig als still und wird nicht gezeichnet. */
const ACTIVE_W = 30;

const VB = { w: 720, h: 392 };
const PAD_TOP = 26;
const PAD_BOTTOM = 26;
const RAIL_X = 344;
const RAIL_W = 14;
const STUB_W = 9;
const LEFT_STUB = 168;
const RIGHT_STUB = VB.w - LEFT_STUB - STUB_W;
const GAP = 7;          // Luft zwischen zwei Bändern an der Außenkante
//: Mindesthöhe eines Bandes. Nicht nur, damit ein kleiner Zweig sichtbar
//: bleibt – zusammen mit GAP ergibt sich daraus der kleinste Abstand zweier
//: Beschriftungen (17), und der muss über der Schriftgröße des Werts (15)
//: liegen, sonst überschreiben sich zwei benachbarte Zahlen.
const MIN_H = 10;

type Branch = {
  key: string;
  icon: IconName;
  watts: number;
  color: string;
  label: string;
  note?: string;
};

type Placed = Branch & { y0: number; y1: number; ry0: number; ry1: number };

/** Außenkante mit Abständen, Schienenkante ohne — die Schiene ist durchgehend. */
function place(items: Branch[], total: number, top: number, height: number): Placed[] {
  if (items.length === 0 || total <= 0) return [];
  const gaps = GAP * (items.length - 1);
  const usable = Math.max(height - gaps, items.length * MIN_H);
  // Erst roh verteilen, dann die Mindesthöhen bei den großen Bändern abziehen.
  // Sonst summieren sich viele Mindesthöhen auf und das Bild läuft über.
  const raw = items.map((b) => (b.watts / total) * usable);
  const lifted = raw.map((h) => Math.max(h, MIN_H));
  const overshoot = lifted.reduce((a, b) => a + b, 0) - usable;
  const slack = lifted.map((h) => Math.max(0, h - MIN_H));
  const slackSum = slack.reduce((a, b) => a + b, 0);
  const heights = lifted.map((h, i) =>
    overshoot > 0 && slackSum > 0 ? h - (slack[i] / slackSum) * overshoot : h,
  );

  let y = top;
  let ry = top;
  return items.map((b, i) => {
    const h = heights[i];
    const rh = (b.watts / total) * height; // an der Schiene lückenlos
    const seg = { ...b, y0: y, y1: y + h, ry0: ry, ry1: ry + rh };
    y += h + GAP;
    ry += rh;
    return seg;
  });
}

/** Band zwischen Außenkante und Schiene. */
function ribbon(x1: number, x2: number, a0: number, a1: number, b0: number, b1: number): string {
  const c1 = x1 + (x2 - x1) * 0.55;
  const c2 = x2 - (x2 - x1) * 0.55;
  return `M ${x1} ${a0} C ${c1} ${a0}, ${c2} ${b0}, ${x2} ${b0}` +
         ` L ${x2} ${b1} C ${c2} ${b1}, ${c1} ${a1}, ${x1} ${a1} Z`;
}

function Ribbon({ seg, side, sweepId }: { seg: Placed; side: "in" | "out"; sweepId: string }) {
  const [x1, x2] = side === "in"
    ? [LEFT_STUB + STUB_W, RAIL_X]
    : [RAIL_X + RAIL_W, RIGHT_STUB];
  const d = side === "in"
    ? ribbon(x1, x2, seg.y0, seg.y1, seg.ry0, seg.ry1)
    : ribbon(x2, x1, seg.y0, seg.y1, seg.ry0, seg.ry1);
  return (
    <g>
      <path d={d} fill={seg.color} fillOpacity="0.17" />
      <path d={d} fill={`url(#${sweepId})`} className="ef-sweep" />
    </g>
  );
}

function Stub({ seg, side }: { seg: Placed; side: "in" | "out" }) {
  const x = side === "in" ? LEFT_STUB : RIGHT_STUB;
  return (
    <rect x={x} y={seg.y0} width={STUB_W} height={Math.max(seg.y1 - seg.y0, 1)}
          rx={Math.min(3, (seg.y1 - seg.y0) / 2)} fill={seg.color} />
  );
}

function Label({ seg, side }: { seg: Placed; side: "in" | "out" }) {
  const mid = (seg.y0 + seg.y1) / 2;
  const anchor = side === "in" ? "end" : "start";
  const x = side === "in" ? LEFT_STUB - 13 : RIGHT_STUB + STUB_W + 13;
  const iconX = side === "in" ? x - 15 : x - 1;
  const tall = seg.y1 - seg.y0 >= 26;
  const animated = useAnimatedNumber(seg.watts);

  return (
    <g>
      {/* Symbol nur bei hohen Bändern. Bei einem 10-px-Streifen säße es genau
          auf der Zahl; die Farbe des Anschlussstücks reicht dort zur
          Zuordnung, eine Überlagerung wäre schlicht unlesbar. */}
      {tall && (
        <g transform={`translate(${iconX}, ${mid - 19})`} color={seg.color}>
          <Icon name={seg.icon} size={15} />
        </g>
      )}
      {tall && (
        <text x={x} y={mid - 1} textAnchor={anchor} className="ef-name">
          {seg.label}
        </text>
      )}
      <text x={x} y={tall ? mid + 15 : mid + 5} textAnchor={anchor} className="ef-val num">
        {fmtW(animated)}
      </text>
      {seg.note && tall && (
        <text x={x} y={mid + 29} textAnchor={anchor} className="ef-note" fill={seg.color}>
          {seg.note}
        </text>
      )}
    </g>
  );
}

export function EnergyFlow({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useI18n();
  const uid = useId().replace(/:/g, "");
  const sweepIn = `sweep-in-${uid}`;
  const sweepOut = `sweep-out-${uid}`;

  const grid = snapshot.grid_power ?? 0;
  const bat = snapshot.battery?.power ?? 0;
  const soc = snapshot.battery?.soc;

  const sources: Branch[] = ([
    { key: "pv", icon: "sun", watts: snapshot.pv_power, color: "var(--c-pv)", label: t("dash.pv") },
    { key: "imp", icon: "grid", watts: Math.max(0, grid), color: "var(--c-grid-import)", label: t("dash.import") },
    {
      key: "bout", icon: "battery", watts: Math.max(0, -bat), color: "var(--c-battery)",
      label: t("dash.battery"),
      note: soc != null ? `${Math.round(soc)} %` : undefined,
    },
  ] as Branch[]).filter((b) => b.watts > ACTIVE_W);

  const loads: Branch[] = ([
    { key: "house", icon: "house", watts: snapshot.house_power, color: "var(--c-house)", label: t("dash.house") },
    { key: "car", icon: "car", watts: snapshot.wallbox_power, color: "var(--c-car)", label: t("dash.car") },
    { key: "water", icon: "water", watts: snapshot.water_power, color: "var(--c-water)", label: t("dash.water") },
    {
      key: "bin", icon: "battery", watts: Math.max(0, bat), color: "var(--c-battery)",
      label: t("dash.battery"),
      note: soc != null ? `${Math.round(soc)} %` : undefined,
    },
    { key: "exp", icon: "export", watts: Math.max(0, -grid), color: "var(--c-grid-export)", label: t("dash.export") },
  ] as Branch[]).filter((b) => b.watts > ACTIVE_W);

  const inSum = sources.reduce((a, b) => a + b.watts, 0);
  const outSum = loads.reduce((a, b) => a + b.watts, 0);
  // Beide Seiten auf denselben Maßstab: Sonst wäre die Schiene links und
  // rechts verschieden hoch, obwohl in Summe dasselbe fließt.
  const total = Math.max(inSum, outSum, 1);

  const railTop = PAD_TOP;
  const railH = VB.h - PAD_TOP - PAD_BOTTOM;
  const inPlaced = place(sources, total, railTop, railH * (inSum / total));
  const outPlaced = place(loads, total, railTop, railH * (outSum / total));

  const idle = sources.length === 0 && loads.length === 0;

  return (
    <svg viewBox={`0 0 ${VB.w} ${VB.h}`} className="ef"
         style={{ width: "100%", height: "auto", display: "block" }}
         role="img" aria-label={t("dash.energyFlow")}>
      <defs>
        {/* Schimmer statt Lauflicht: eine breite, weiche Aufhellung, die
            langsam durch das Band wandert. */}
        <linearGradient id={sweepIn} gradientUnits="userSpaceOnUse"
                        x1={LEFT_STUB} y1="0" x2={RAIL_X} y2="0">
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0.30" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
          <animateTransform attributeName="gradientTransform" type="translate"
                            from="-220 0" to="220 0" dur="4.5s" repeatCount="indefinite" />
        </linearGradient>
        <linearGradient id={sweepOut} gradientUnits="userSpaceOnUse"
                        x1={RAIL_X + RAIL_W} y1="0" x2={RIGHT_STUB} y2="0">
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0.30" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
          <animateTransform attributeName="gradientTransform" type="translate"
                            from="-220 0" to="220 0" dur="4.5s" repeatCount="indefinite" />
        </linearGradient>
      </defs>

      <text x={LEFT_STUB + STUB_W} y="14" textAnchor="end" className="ef-head">
        {t("dash.barSources")}
      </text>
      <text x={RIGHT_STUB} y="14" textAnchor="start" className="ef-head">
        {t("dash.barLoads")}
      </text>

      {inPlaced.map((s) => <Ribbon key={s.key} seg={s} side="in" sweepId={sweepIn} />)}
      {outPlaced.map((s) => <Ribbon key={s.key} seg={s} side="out" sweepId={sweepOut} />)}

      {/* Sammelschiene: der Punkt, auf den geregelt wird */}
      <rect x={RAIL_X} y={railTop} width={RAIL_W} height={railH}
            rx={RAIL_W / 2} className="ef-rail" />
      {!idle && (
        <rect x={RAIL_X} y={railTop} width={RAIL_W} height={railH * (Math.max(inSum, outSum) / total)}
              rx={RAIL_W / 2} className="ef-rail-fill" />
      )}

      {inPlaced.map((s) => <Stub key={s.key} seg={s} side="in" />)}
      {outPlaced.map((s) => <Stub key={s.key} seg={s} side="out" />)}
      {inPlaced.map((s) => <Label key={s.key} seg={s} side="in" />)}
      {outPlaced.map((s) => <Label key={s.key} seg={s} side="out" />)}

      {idle && (
        <text x={VB.w / 2} y={VB.h / 2 + 4} textAnchor="middle" className="ef-idle">
          {t("dash.idle")}
        </text>
      )}
    </svg>
  );
}
