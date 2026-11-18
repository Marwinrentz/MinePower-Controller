/** Isometrische Bilder für den Energiefluss: jedes Gerät steht auf einer
 *  farbigen Kachel (Sonne, Netz, Speicher, Haus, Auto, Warmwasser).
 *
 *  Alles ist selbst gezeichnet und wird aus 3D-Koordinaten berechnet
 *  (`iso()`), damit die Perspektive überall exakt gleich ist und keine
 *  fremden Bilddateien nötig sind. Ein Bild ist 120 × 100 Einheiten groß;
 *  die Kachel liegt in der Mitte bei z = 0, x und y laufen von −28 bis 28.
 *
 *  Leichte Bewegung zeigt, was gerade passiert: die Wallbox-LED pulsiert beim
 *  Laden, der Heizstab glüht, der Speicher zeigt seinen Füllstand.
 */
import { useId, type ReactNode } from "react";

type P3 = [number, number, number];

const CX = 60;
const CY = 58;
const T = 28;   // halbe Kachelkante
const D = 7;    // Kacheldicke

function iso([x, y, z]: P3): [number, number] {
  return [CX + (x - y) * 0.866, CY + (x + y) * 0.5 - z];
}

function pts(...p: P3[]): string {
  return p.map((q) => iso(q).map((v) => v.toFixed(1)).join(",")).join(" ");
}

function lerp(a: P3, b: P3, t: number): P3 {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

function line(a: P3, b: P3, props: Record<string, unknown> = {}, key?: string | number) {
  const [x1, y1] = iso(a);
  const [x2, y2] = iso(b);
  return <line key={key} x1={x1} y1={y1} x2={x2} y2={y2} {...props} />;
}

/** Quader – sichtbar sind Deckel, linke Front (y max) und rechte Front (x max). */
function Box({ x, y, z, dx, dy, dz, top, left, right, stroke }: {
  x: number; y: number; z: number; dx: number; dy: number; dz: number;
  top: string; left: string; right: string; stroke?: string;
}) {
  const x1 = x + dx, y1 = y + dy, z1 = z + dz;
  const s = stroke ? { stroke, strokeWidth: 0.4, strokeLinejoin: "round" as const } : {};
  return (
    <g>
      <polygon points={pts([x, y1, z], [x1, y1, z], [x1, y1, z1], [x, y1, z1])} fill={left} {...s} />
      <polygon points={pts([x1, y, z], [x1, y1, z], [x1, y1, z1], [x1, y, z1])} fill={right} {...s} />
      <polygon points={pts([x, y, z1], [x1, y, z1], [x1, y1, z1], [x, y1, z1])} fill={top} {...s} />
    </g>
  );
}

/** Viereck auf einer Ebene, gegeben durch vier 3D-Ecken. */
function Quad({ p, fill, ...rest }: { p: [P3, P3, P3, P3]; fill: string } & Record<string, unknown>) {
  return <polygon points={pts(...p)} fill={fill} {...rest} />;
}

/** Solarmodul mit Zellraster auf einer beliebigen Fläche (a→b unten, d→c oben). */
function Panel({ a, b, c, d, cols, rows }: { a: P3; b: P3; c: P3; d: P3; cols: number; rows: number }) {
  const cells: ReactNode[] = [];
  for (let i = 1; i < cols; i++) cells.push(line(lerp(a, b, i / cols), lerp(d, c, i / cols), { className: "fa-cell" }, `c${i}`));
  for (let j = 1; j < rows; j++) cells.push(line(lerp(a, d, j / rows), lerp(b, c, j / rows), { className: "fa-cell" }, `r${j}`));
  return (
    <g>
      <Quad p={[a, b, c, d]} fill="#1d3a72" stroke="#e6ebf1" strokeWidth={1.1} strokeLinejoin="round" />
      {cells}
      <Quad p={[lerp(a, d, 0.55), lerp(b, c, 0.55), lerp(b, c, 0.95), lerp(a, d, 0.95)]} fill="#ffffff" opacity={0.08} />
    </g>
  );
}

const TILE_COLORS = {
  green: ["#a3cf3c", "#4f86c6", "#7ea42b", "#3c6aa6"],
  red: ["#ec4a63", "#6f82d6", "#c23a50", "#5266b4"],
} as const;

function Tile({ tone }: { tone: keyof typeof TILE_COLORS }) {
  const id = useId().replace(/:/g, "");
  const [from, to, sideL, sideR] = TILE_COLORS[tone];
  return (
    <g className="fa-tile">
      <defs>
        <linearGradient id={`t${id}`} x1="0" y1="0.3" x2="1" y2="0.7">
          <stop offset="0.12" stopColor={from} />
          <stop offset="0.9" stopColor={to} />
        </linearGradient>
        <linearGradient id={`l${id}`} x1="0" x2="1">
          <stop offset="0" stopColor={sideL} />
          <stop offset="1" stopColor={tone === "red" ? "#9a4f86" : "#5f8f55"} />
        </linearGradient>
      </defs>
      <polygon points={pts([-T, T, 0], [T, T, 0], [T, T, -D], [-T, T, -D])} fill={`url(#l${id})`} />
      <polygon points={pts([T, -T, 0], [T, T, 0], [T, T, -D], [T, -T, -D])} fill={sideR} />
      <polygon points={pts([-T, -T, 0], [T, -T, 0], [T, T, 0], [-T, T, 0])} fill={`url(#t${id})`} />
    </g>
  );
}

function Art({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <svg className={`fa ${className}`} viewBox="0 0 120 100" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

/* ------------------------------------------------------------------ Bilder */

export function PvArt({ active }: { active: boolean }) {
  const a: P3 = [-17, 13, 6], b: P3 = [17, 13, 6], c: P3 = [17, -12, 25], d: P3 = [-17, -12, 25];
  return (
    <Art className={active ? "on" : ""}>
      <Tile tone="green" />
      {/* Gestell */}
      {line([-14, -10, 0], [-14, -10, 23], { className: "fa-leg" })}
      {line([14, -10, 0], [14, -10, 23], { className: "fa-leg" })}
      {line([-14, 11, 0], [-14, 11, 7], { className: "fa-leg" })}
      {line([14, 11, 0], [14, 11, 7], { className: "fa-leg" })}
      {/* Modulkante */}
      <Quad p={[a, b, [17, 13, 4.5], [-17, 13, 4.5]]} fill="#c3ccd6" />
      <Quad p={[b, c, [17, -12, 23.5], [17, 13, 4.5]]} fill="#aab4c0" />
      <Panel a={a} b={b} c={c} d={d} cols={6} rows={4} />
      {active && <circle className="fa-glint" cx={iso(lerp(d, c, 0.8))[0]} cy={iso(lerp(d, c, 0.8))[1] + 3} r={2.2} />}
    </Art>
  );
}

export function GridArt() {
  const legs: P3[] = [[-7, -7, 0], [7, -7, 0], [7, 7, 0], [-7, 7, 0]];
  const top: P3[] = [[-2.2, -2.2, 44], [2.2, -2.2, 44], [2.2, 2.2, 44], [-2.2, 2.2, 44]];
  const at = (z: number) => legs.map((l, i) => lerp(l, top[i], z / 44));
  const st = { className: "fa-steel" };
  const parts: ReactNode[] = [];
  legs.forEach((l, i) => parts.push(line(l, top[i], st, `leg${i}`)));
  [0, 11, 22, 33, 44].forEach((z, k) => {
    const r = at(z);
    for (let i = 0; i < 4; i++) parts.push(line(r[i], r[(i + 1) % 4], st, `ring${k}${i}`));
    if (k < 4) {
      const n = at([0, 11, 22, 33, 44][k + 1]);
      // Kreuzverstrebung auf den beiden sichtbaren Seiten
      parts.push(line(r[3], n[2], st, `x${k}a`), line(r[2], n[3], st, `x${k}b`));
      parts.push(line(r[1], n[2], st, `x${k}c`), line(r[2], n[1], st, `x${k}d`));
    }
  });
  // Ausleger mit Isolatoren
  const arms: [number, number][] = [[38, 17], [46, 12]];
  arms.forEach(([z, w], k) => {
    parts.push(line([-w, 0, z], [w, 0, z], st, `arm${k}`));
    parts.push(line([-w, 0, z], [-2, 0, z + 4], st, `armA${k}`), line([w, 0, z], [2, 0, z + 4], st, `armB${k}`));
    [-w, w].forEach((x, j) => parts.push(line([x, 0, z], [x, 0, z - 3.5], { className: "fa-insul" }, `ins${k}${j}`)));
  });
  parts.push(line([0, 0, 44], [0, 0, 54], st, "spike"));
  // Leitungen laufen nach hinten aus dem Bild
  const wires: ReactNode[] = [];
  arms.forEach(([z, w], k) => [-w, w].forEach((x, j) => {
    wires.push(line([x, -34, z - 1.5], [x, 0, z - 3.5], { className: "fa-wire" }, `wb${k}${j}`));
    wires.push(line([x, 0, z - 3.5], [x, 30, z - 9], { className: "fa-wire" }, `wf${k}${j}`));
  }));
  return (
    <Art>
      <Tile tone="red" />
      {/* feines Raster wie bei einer Landkarte */}
      {[-14, 0, 14].map((v) => line([v, -T, 0], [v, T, 0], { className: "fa-map" }, `mx${v}`))}
      {[-14, 0, 14].map((v) => line([-T, v, 0], [T, v, 0], { className: "fa-map" }, `my${v}`))}
      {wires}
      {parts}
    </Art>
  );
}

export function BatteryArt({ soc, active }: { soc: number | null; active: boolean }) {
  const h = 38;
  const lvl = Math.max(0, Math.min(1, (soc ?? 0) / 100));
  // Füllstandsbalken auf der linken Front
  const barX0 = -6, barX1 = 6, barZ0 = 7, barZ1 = 29;
  const bolt: [number, number][] = [[2.5, -7], [-4, 1], [0, 1], [-2.5, 7], [4, -1], [0, -1]];
  return (
    <Art className={active ? "on" : ""}>
      <Tile tone="green" />
      <Box x={-11} y={-11} z={0} dx={22} dy={22} dz={h} top="#40454e" left="#f3f4f6" right="#d3d8de" />
      {/* dunkles Band oben */}
      <Quad p={[[-11, 11, h - 4], [11, 11, h - 4], [11, 11, h], [-11, 11, h]]} fill="#40454e" />
      <Quad p={[[11, -11, h - 4], [11, 11, h - 4], [11, 11, h], [11, -11, h]]} fill="#33373f" />
      <polygon points={pts(...bolt.map(([x, y]) => [x, y, h] as P3))} fill="#f5c518" />
      {soc != null && (
        <g>
          <Quad p={[[barX0, 11, barZ0], [barX1, 11, barZ0], [barX1, 11, barZ1], [barX0, 11, barZ1]]} fill="#dfe3e8" />
          <Quad className="fa-soc" fill={lvl < 0.15 ? "#e5484d" : "#7cc243"}
            p={[[barX0, 11, barZ0], [barX1, 11, barZ0], [barX1, 11, barZ0 + (barZ1 - barZ0) * lvl], [barX0, 11, barZ0 + (barZ1 - barZ0) * lvl]]} />
        </g>
      )}
    </Art>
  );
}

export function HouseArt() {
  const W = 20, Dp = 13, H = 21, R = 37, O = 2.5;
  const wall = { left: "#f4f5f7", right: "#dde2e8" };
  const win = (x0: number, x1: number, z0: number, z1: number, k: string) => (
    <Quad key={k} p={[[x0, Dp, z0], [x1, Dp, z0], [x1, Dp, z1], [x0, Dp, z1]]} fill="#596273" />
  );
  const eave = (x: number, t: number): P3 => [x, (Dp + O) * t, R - (R - H + 1) * t];
  return (
    <Art>
      <Tile tone="green" />
      <Box x={-W} y={-Dp} z={0} dx={2 * W} dy={2 * Dp} dz={H} top="#ccd2d9" left={wall.left} right={wall.right} />
      {/* Giebel */}
      <polygon points={pts([W, -Dp, H], [W, Dp, H], [W, 0, R])} fill={wall.right} />
      {/* Fenster und Tür vorn, Fenster im Giebel */}
      {win(-16, -9, 9, 17, "w1")}
      {win(-5, 2, 9, 17, "w2")}
      {win(8, 14, 0, 14, "door")}
      <Quad p={[[W, -6, 8], [W, 0, 8], [W, 0, 15], [W, -6, 15]]} fill="#596273" />
      <Quad p={[[W, 3, 8], [W, 8, 8], [W, 8, 15], [W, 3, 15]]} fill="#596273" />
      {/* Dach: hintere Fläche, vordere Fläche mit Modulen */}
      <Quad p={[[-W - O, -Dp - O, H - 1], [W + O, -Dp - O, H - 1], [W + O, 0, R], [-W - O, 0, R]]} fill="#5a606b" />
      <Quad p={[[-W - O, 0, R], [W + O, 0, R], eave(W + O, 1), eave(-W - O, 1)]} fill="#3f4550" />
      <Quad p={[eave(W + O, 1), eave(-W - O, 1), [-W - O, Dp + O, H - 2], [W + O, Dp + O, H - 2]]} fill="#2d323a" />
      <Panel a={eave(-W + 1, 0.86)} b={eave(W - 1, 0.86)} c={eave(W - 1, 0.12)} d={eave(-W + 1, 0.12)} cols={6} rows={2} />
    </Art>
  );
}

export function CarArt({ active, present }: { active: boolean; present: boolean }) {
  // Rad als Ellipse in der Seitenfläche y = 9 (Achse x, z).
  const wheel = (x: number, k: string) => {
    const p: P3[] = [];
    for (let i = 0; i < 18; i++) {
      const a = (i / 18) * Math.PI * 2;
      p.push([x + Math.cos(a) * 4, 9.2, 4 + Math.sin(a) * 4]);
    }
    return <polygon key={k} points={pts(...p)} fill="#2a2e35" />;
  };
  return (
    <Art className={`${active ? "on" : ""}`}>
      <Tile tone="green" />
      {/* Wallbox an der Säule hinten */}
      <Box x={14} y={-22} z={0} dx={3} dy={3} dz={24} top="#9aa3ad" left="#c4cbd3" right="#aab3bd" />
      <Box x={12} y={-19} z={13} dx={7} dy={3} dz={10} top="#e9edf1" left="#ffffff" right="#d7dde3" />
      <circle className="fa-led" cx={iso([15.5, -16, 20])[0]} cy={iso([15.5, -16, 20])[1]} r={1.3} />
      {present && (
        <g>
          {active && <path className="fa-cable" d={`M ${iso([15, -16, 15]).join(" ")} Q ${iso([16, -6, 2]).join(" ")} ${iso([18, 2, 10]).join(" ")}`} />}
          <Box x={-21} y={-9} z={3} dx={42} dy={18} dz={8} top="#e9edf2" left="#f7f8fa" right="#d5dbe2" />
          <Box x={-14} y={-8} z={11} dx={17} dy={16} dz={7} top="#f1f3f6" left="#3b4452" right="#4b5563" />
          {/* schräge Frontscheibe und Fenstersäulen */}
          <Quad p={[[3, -8, 18], [3, 8, 18], [10, 8, 11], [10, -8, 11]]} fill="#4b5563" />
          <polygon points={pts([3, 8, 18], [10, 8, 11], [3, 8, 11])} fill="#3b4452" />
          <Quad p={[[-14, 8, 11], [-12, 8, 11], [-12, 8, 18], [-14, 8, 18]]} fill="#f7f8fa" />
          <Quad p={[[-5, 8, 11], [-3.5, 8, 11], [-3.5, 8, 18], [-5, 8, 18]]} fill="#f7f8fa" />
          {wheel(-12, "w1")}
          {wheel(12, "w2")}
          {/* Scheinwerfer */}
          <Quad p={[[21, -7, 8], [21, -3, 8], [21, -3, 10], [21, -7, 10]]} fill="#fde68a" />
          <Quad p={[[21, 3, 8], [21, 7, 8], [21, 7, 10], [21, 3, 10]]} fill="#fde68a" />
        </g>
      )}
      {!present && (
        <Quad p={[[-21, -9, 0.2], [21, -9, 0.2], [21, 9, 0.2], [-21, 9, 0.2]]} fill="none"
          stroke="#ffffff" strokeOpacity={0.7} strokeDasharray="2.5 2.5" strokeWidth={0.9} />
      )}
    </Art>
  );
}

export function WaterArt({ active, temp }: { active: boolean; temp: number | null }) {
  const id = useId().replace(/:/g, "");
  const r = 11, h = 36;
  const [cx, cyB] = iso([0, 0, 0]);
  const cyT = cyB - h;
  const rx = r * 1.2247, ry = r * 0.7071;
  // Thermometer vorn: 30–70 °C füllen den Balken
  const lvl = temp == null ? 0 : Math.max(0.08, Math.min(1, (temp - 30) / 40));
  const tx = cx - 3, tz0 = cyB - 8, tz1 = cyT + 10;
  return (
    <Art className={active ? "on" : ""}>
      <Tile tone="green" />
      <defs>
        <linearGradient id={`c${id}`} x1="0" x2="1">
          <stop offset="0" stopColor="#c9d0d8" />
          <stop offset="0.35" stopColor="#ffffff" />
          <stop offset="1" stopColor="#aeb7c2" />
        </linearGradient>
      </defs>
      {/* Rohre: kalt rein, warm raus */}
      <path className="fa-pipe cold" d={`M ${cx + 4} ${cyB + 3} q 8 4 18 2`} />
      <path className="fa-pipe hot" d={`M ${cx + 9} ${cyB - 10} q 9 3 14 12`} />
      <path d={`M ${cx - rx} ${cyT} V ${cyB} A ${rx} ${ry} 0 0 0 ${cx + rx} ${cyB} V ${cyT} Z`} fill={`url(#c${id})`} />
      <ellipse cx={cx} cy={cyT} rx={rx} ry={ry} fill="#e3e8ed" stroke="#c4ccd5" strokeWidth={0.5} />
      <ellipse cx={cx} cy={cyT - 0.2} rx={rx * 0.42} ry={ry * 0.42} fill="#cfd6de" />
      {/* Glühen, wenn der Heizstab läuft */}
      <ellipse className="fa-heat" cx={cx} cy={cyB - 6} rx={rx * 0.8} ry={4} />
      <rect x={tx} y={tz1} width={6} height={tz0 - tz1} rx={3} fill="#e5e9ee" stroke="#b7c0ca" strokeWidth={0.5} />
      <rect className="fa-temp" x={tx + 1.3} y={tz0 - (tz0 - tz1) * lvl + 1} width={3.4} height={(tz0 - tz1) * lvl - 2} rx={1.7} />
      <circle cx={tx + 3} cy={tz0 + 1} r={3.4} className="fa-temp" />
    </Art>
  );
}
