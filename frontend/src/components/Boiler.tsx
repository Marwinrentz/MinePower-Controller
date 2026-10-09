/** Warmwasserspeicher als Grafik.
 *
 *  Ein Zylinder im Anschnitt: Die Wassersäule steigt mit der Temperatur
 *  (15 °C leer … 80 °C voll). Der Farbverlauf hängt an der Skala, nicht an
 *  der Säule – bei 30 °C ist die Oberfläche also blau, bei 65 °C rot. Links
 *  eine Temperaturskala, rechts die Marken für Ziel-, Boost- und
 *  Puffertemperatur. Der Heizstab unten glüht, solange Leistung fließt, die
 *  Oberfläche bewegt sich und Bläschen steigen auf – je mehr Leistung, desto
 *  mehr Bläschen. Heizt das Gerät im eigenen Programm, glüht der Stab in
 *  Warnfarbe.
 */
import { useId } from "react";
import { fmtTemp } from "../lib/format";
import { spreadLabels } from "../lib/spread";

const T_MIN = 15;
const T_MAX = 80;
const INNER_TOP = 38;
const INNER_BOTTOM = 262;
const INNER_X = 58;
const INNER_W = 100;

const yFor = (temp: number) => {
  const f = Math.max(0, Math.min(1, (temp - T_MIN) / (T_MAX - T_MIN)));
  return INNER_BOTTOM - f * (INNER_BOTTOM - INNER_TOP);
};
const offsetFor = (temp: number) => (temp - T_MIN) / (T_MAX - T_MIN);

export type BoilerMark = { temp: number; label: string; kind: "target" | "boost" | "buffer" };

/** Wasserkörper mit welliger Oberfläche, breiter als das Fenster, damit er
 *  sich beim Heizen seitlich verschieben kann (eine Periode = 50). */
function waterPath(level: number): string {
  let d = `M${INNER_X - 50} ${level}`;
  for (let i = 0; i < 8; i++) d += i === 0 ? " q12.5 -3.5 25 0" : " t25 0";
  return `${d} V${INNER_BOTTOM + 4} H${INNER_X - 50} Z`;
}

export function Boiler({ temp, power, maxPower, marks, label, own }: {
  temp: number | null; power: number; maxPower: number; marks: BoilerMark[]; label: string;
  /** Gerät heizt im eigenen Programm – Heizstab in Warnfarbe. */
  own?: boolean;
}) {
  const uid = useId().replace(/:/g, "");
  const id = (name: string) => `${name}-${uid}`;
  const url = (name: string) => `url(#${id(name)})`;
  const heating = power > 50;
  const level = temp == null ? INNER_BOTTOM : yFor(temp);
  // Bläschen: 2–7 je nach Anteil an der Höchstleistung
  const bubbles = heating ? Math.max(2, Math.min(7, Math.round((power / Math.max(maxPower, 1)) * 7))) : 0;
  const ys = marks.map((m) => yFor(m.temp));
  const textY = spreadLabels(ys, 16);

  return (
    <svg className={`boiler ${heating ? "heating" : ""} ${own ? "own" : ""}`} viewBox="0 0 280 300" role="img" aria-label={label}>
      <defs>
        {/* Zylinder-Schattierung: Ränder dunkler, Mitte heller */}
        <linearGradient id={id("shell")} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="var(--boiler-shade)" />
          <stop offset="0.38" stopColor="var(--boiler-hi)" />
          <stop offset="1" stopColor="var(--boiler-shade)" />
        </linearGradient>
        <linearGradient id={id("water")} gradientUnits="userSpaceOnUse" x1="0" y1={INNER_BOTTOM} x2="0" y2={INNER_TOP}>
          <stop offset="0" stopColor="var(--water-cold)" />
          <stop offset={offsetFor(32)} stopColor="var(--water-cold)" />
          <stop offset={offsetFor(46)} stopColor="var(--water-warm)" />
          <stop offset={offsetFor(62)} stopColor="var(--water-hot)" />
          <stop offset="1" stopColor="var(--water-hot)" />
        </linearGradient>
        <linearGradient id={id("sheen")} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.25" stopColor="#fff" stopOpacity="0.22" />
          <stop offset="0.45" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <radialGradient id={id("glow")}>
          <stop offset="0" stopColor="#ff8a3d" stopOpacity="0.75" />
          <stop offset="1" stopColor="#ff8a3d" stopOpacity="0" />
        </radialGradient>
        <clipPath id={id("inner")}>
          <rect x={INNER_X} y={INNER_TOP} width={INNER_W} height={INNER_BOTTOM - INNER_TOP} rx="30" />
        </clipPath>
      </defs>

      {/* Temperaturskala links */}
      <g className="b-scale">
        {[20, 40, 60, 80].map((v) => (
          <g key={v}>
            <path d={`M32 ${yFor(v)} H40`} />
            <text x="28" y={yFor(v) + 4} textAnchor="end">{v}°</text>
          </g>
        ))}
      </g>

      {/* Anschlüsse oben: kalt (mit Tauchrohr) und warm */}
      <path className="b-pipe cold" d="M84 26 V10" />
      <path className="b-pipe hot" d="M132 26 V10" />

      {/* Speicher: Mantel, Füße, Sichtfenster */}
      <path className="b-foot" d="M66 274 v12 M150 274 v12" />
      <rect className="b-shell" x="46" y="24" width="124" height="252" rx="42" fill={url("shell")} />
      <rect className="b-inner" x={INNER_X} y={INNER_TOP} width={INNER_W} height={INNER_BOTTOM - INNER_TOP} rx="30" />

      {/* Wasser nach Temperatur */}
      <g clipPath={url("inner")}>
        {temp != null && <path className="b-water" d={waterPath(level)} fill={url("water")} />}
        {heating && <ellipse className="b-glow" cx="100" cy="236" rx="62" ry="22" fill={url("glow")} />}
        {Array.from({ length: bubbles }).map((_, i) => (
          <circle key={i} className="b-bubble" cx={72 + ((i * 19) % 76)} cy={INNER_BOTTOM - 30} r={2.2 + (i % 3) * 0.9}
                  style={{ animationDelay: `${(i * 0.41) % 2.2}s`, ["--rise" as string]: `${Math.max(36, INNER_BOTTOM - level - 16)}px` }} />
        ))}
        <rect x={INNER_X} y={INNER_TOP} width={INNER_W} height={INNER_BOTTOM - INNER_TOP} fill={url("sheen")} pointerEvents="none" />
      </g>

      {/* Heizstab: Flansch links, U-förmig in den Speicher */}
      <g className="b-heater">
        <path className="b-rod" d="M44 242 H128 C138 242 138 228 128 228 H70" />
        <rect className="b-flange" x="34" y="230" width="14" height="20" rx="3" />
      </g>

      {/* Marken rechts neben dem Speicher */}
      {marks.map((m, i) => (
        <g key={m.kind} className={`b-mark ${m.kind}`}>
          <path d={`M${INNER_X + INNER_W - 8} ${ys[i]} H176 L182 ${textY[i]} H186`} />
          <text x="190" y={textY[i] + 4}>{m.label}</text>
        </g>
      ))}

      {/* Temperatur als Plakette in der Mitte – lesbar über Wasser und Luft */}
      <g className="b-readout">
        <rect x="64" y="128" width="88" height="38" rx="19" />
        <text x="108" y="153.5" textAnchor="middle">{temp != null ? fmtTemp(temp) : "–"}</text>
      </g>
    </svg>
  );
}
