/** Batterie als Grafik – bewusst schlicht.
 *
 *  Ein Umriss, eine flache Füllung, eine Linie für die Reserve. Mehr braucht
 *  es nicht, um auf einen Blick zu sehen: wie voll, ob er lädt oder entlädt,
 *  und wie weit es noch bis zur Reserve ist. Lädt er aus dem Netz, ist die
 *  Füllung blau statt gelb. Bewegung nur als leises Pulsieren des Pfeils
 *  (prefers-reduced-motion: aus).
 */
import { spreadLabels } from "../lib/spread";

export type GaugeMark = { soc: number; label: string; kind: "reserve" | "priority" | "inverter" };

const TOP = 40;
const BOTTOM = 262;
const X = 66;
const W = 92;
const yFor = (soc: number) => BOTTOM - (Math.max(0, Math.min(100, soc)) / 100) * (BOTTOM - TOP);

export function BatteryGauge({ soc, power, gridCharging, marks, label }: {
  soc: number | null; power: number; gridCharging?: boolean; marks: GaugeMark[]; label: string;
}) {
  const flow = power > 30 ? "up" : power < -30 ? "down" : "none";
  const level = soc == null ? BOTTOM : yFor(soc);
  const shown = marks.filter((m) => m.kind !== "inverter");
  const ys = shown.map((m) => yFor(m.soc));
  const textY = spreadLabels(ys, 18);
  const textInFill = level < 140;

  return (
    <svg className={`bgauge flow-${flow} ${gridCharging ? "grid" : ""}`} viewBox="0 0 280 300" role="img" aria-label={label}>
      <rect className="bg-cap" x={X + W / 2 - 18} y="16" width="36" height="12" rx="4" />
      <rect className="bg-cell" x={X - 10} y="28" width={W + 20} height={BOTTOM - TOP + 22} rx="22" />
      {soc != null && soc > 0.5 && (
        <rect className="bg-fill" x={X} y={level} width={W} height={Math.max(0, BOTTOM - level)} rx="12" />
      )}

      {shown.map((m, i) => (
        <g key={m.kind} className={`bg-mark ${m.kind}`}>
          <path d={`M${X} ${ys[i]} H${X + W}`} />
          <path className="bg-mark-tick" d={`M${X + W + 14} ${ys[i]} L${X + W + 20} ${textY[i]} H${X + W + 26}`} />
          <text x={X + W + 30} y={textY[i] + 4}>{m.label}</text>
        </g>
      ))}

      <text className={`bg-soc ${textInFill ? "on-fill" : ""}`} x={X + W / 2} y={textInFill ? level + 44 : level - 16}
            textAnchor="middle">{soc != null ? `${Math.round(soc)} %` : "–"}</text>
      {flow !== "none" && (
        <path className="bg-arrow" transform={`translate(${X + W / 2 - 9} ${textInFill ? level + 54 : level - 70})`}
              d={flow === "up" ? "M9 16V2m0 0-6 6m6-6 6 6" : "M9 2v14m0 0-6-6m6 6 6-6"} />
      )}
    </svg>
  );
}
