/** Garage mit Auto – der Zustand des Ladepunkts als Bild.
 *
 *  • lädt: Auto steht in der Garage, Kabel gesteckt, Energie läuft sichtbar
 *    durchs Kabel, der Ring an der Ladeklappe pulsiert, die Wallbox leuchtet.
 *  • wartet: Auto steht ruhig, Kabel gesteckt, Ring und Wallbox gedimmt.
 *  • weg (nicht angesteckt, nicht erreichbar, keine Verbindung): Das Auto
 *    fährt beim Zustandswechsel durchs offene Tor hinaus; danach bleibt ein
 *    gestrichelter Umriss auf dem freien Stellplatz. Kommt es zurück, fährt
 *    es wieder hinein.
 *
 *  Neutrale Crossover-Silhouette mit den Proportionen eines Model Y
 *  (4,75 m lang, 1,62 m hoch, 2,89 m Radstand), ohne Markenzeichen. Die
 *  Ladeklappe sitzt wie beim Tesla hinten links in der Rückleuchte – bei
 *  einem nach rechts gewandten Auto also auf der sichtbaren Seite, zur
 *  Wallbox hin. Bewegung respektiert prefers-reduced-motion (siehe CSS).
 */
import { useEffect, useId, useRef, useState } from "react";

export type GarageState = "charging" | "waiting" | "asleep" | "away" | "unknown";

type Phase = "present" | "leaving" | "gone" | "arriving";

/** Karosserie: Heck links, Front rechts, Boden bei y = 208. */
const BODY =
  "M94 197 L88 190 C85 184 84 174 86 166 L91 153 " +
  "C112 140 152 128 198 127 C216 127 229 130 240 137 L263 152 " +
  "C282 155 298 159 307 164 C313 168 316 174 316 182 L315 192 " +
  "C315 195 313 197 309 197 L292 197 A25 25 0 1 0 244 197 " +
  "L152 197 A25 25 0 1 0 104 197 Z";

const GLASS = "M107 152 C128 141 160 134 197 134 C213 134 225 136 234 142 L251 155 L114 156 Z";

const WHEELS = [128, 268];

function spokes(cx: number, cy: number): string {
  let d = "";
  for (let i = 0; i < 5; i++) {
    const a = (i * 72 - 90) * (Math.PI / 180);
    const x1 = cx + Math.cos(a) * 5.5, y1 = cy + Math.sin(a) * 5.5;
    const x2 = cx + Math.cos(a) * 11.5, y2 = cy + Math.sin(a) * 11.5;
    d += `M${x1.toFixed(1)} ${y1.toFixed(1)} L${x2.toFixed(1)} ${y2.toFixed(1)} `;
  }
  return d;
}

export function Garage({ state, soc, label }: { state: GarageState; soc?: number | null; label: string }) {
  const present = state === "charging" || state === "waiting" || state === "asleep";
  const [phase, setPhase] = useState<Phase>(present ? "present" : "gone");
  const prev = useRef(present);

  useEffect(() => {
    if (prev.current === present) return;
    prev.current = present;
    setPhase(present ? "arriving" : "leaving");
    const timer = window.setTimeout(() => setPhase(present ? "present" : "gone"), 1600);
    return () => window.clearTimeout(timer);
  }, [present]);

  // Eindeutige IDs: SVG-Verläufe gelten dokumentweit.
  const uid = useId().replace(/:/g, "");
  const id = (name: string) => `${name}-${uid}`;
  const url = (name: string) => `url(#${id(name)})`;
  const showCar = phase !== "gone";
  const plugged = phase === "present" && present;

  return (
    <svg className={`garage state-${state} phase-${phase}`} viewBox="0 0 400 236" role="img" aria-label={label}>
      <defs>
        <linearGradient id={id("wall")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--garage-bg)" stopOpacity="0.55" />
          <stop offset="1" stopColor="var(--garage-bg)" />
        </linearGradient>
        <linearGradient id={id("body")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--car-body-hi)" />
          <stop offset="0.55" stopColor="var(--car-body)" />
          <stop offset="1" stopColor="var(--car-body)" />
        </linearGradient>
        <linearGradient id={id("glass")} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--art-glass-a)" />
          <stop offset="1" stopColor="var(--art-glass-b)" />
        </linearGradient>
        <linearGradient id={id("cone")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--garage-light)" stopOpacity="1" />
          <stop offset="1" stopColor="var(--garage-light)" stopOpacity="0" />
        </linearGradient>
        <radialGradient id={id("shadow")}>
          <stop offset="0" stopColor="#000" stopOpacity="0.32" />
          <stop offset="1" stopColor="#000" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={id("glow")}>
          <stop offset="0" stopColor="var(--c-car)" stopOpacity="0.9" />
          <stop offset="1" stopColor="var(--c-car)" stopOpacity="0" />
        </radialGradient>
        <clipPath id={id("inside")}>
          <rect x="0" y="0" width="400" height="236" />
        </clipPath>
      </defs>

      {/* Garage: Innenraum, Lichtkegel, Dach, Rückwand */}
      <path className="g-room" d="M30 98 L200 40 L370 98 V208 H30 Z" fill={url("wall")} />
      <path className="g-cone" d="M192 66 L208 66 L262 208 L138 208 Z" fill={url("cone")} />
      <path className="g-lamp" d="M188 60 h24 l-4 7 h-16 Z" />
      <path className="g-roof" d="M18 104 L200 40 L382 104" />
      <path className="g-wall" d="M30 102 V208" />
      {/* Offenes Tor: nur der Sturz rechts, darunter fährt das Auto hinaus */}
      <path className="g-wall" d="M370 102 V122" />
      <rect className="g-floor" x="8" y="207" width="384" height="5" rx="2.5" />

      {/* Freier Stellplatz: gestrichelter Umriss – bei 'unbekannt" mit Fragezeichen */}
      {phase === "gone" && (
        <g className="g-ghost">
          <path d={BODY} />
          {WHEELS.map((x) => <circle key={x} cx={x} cy={190} r={19} />)}
        </g>
      )}
      {phase === "gone" && state === "unknown" && (
        <text className="g-unknown" x="200" y="170" textAnchor="middle">?</text>
      )}

      {/* Wallbox mit Lichtleiste */}
      <g className="g-box">
        <rect x="40" y="120" width="26" height="46" rx="9" />
        <rect className="g-led" x="51" y="128" width="4" height="22" rx="2" />
      </g>

      {/* Kabel – nur, solange das Auto angesteckt ist */}
      {plugged && (
        <g className="g-cable">
          <path className="g-cable-base" d="M53 166 C 52 206, 92 210, 97 168" />
          <path className="g-cable-flow" d="M53 166 C 52 206, 92 210, 97 168" />
        </g>
      )}

      {/* Auto */}
      {showCar && (
        <g clipPath={url("inside")}>
          <g className="g-car">
            <ellipse cx="198" cy="208" rx="126" ry="7" fill={url("shadow")} />
            <path className="g-body" d={BODY} fill={url("body")} />
            <path className="g-glass" d={GLASS} fill={url("glass")} />
            <path className="g-pillar" d="M190 134 L187 156" />
            <path className="g-seam" d="M238 157 L237 196 M187 157 L187 197" />
            <path className="g-handle" d="M224 163 h9 M174 163 h9" />
            <path className="g-mirror" d="M247 150 l10 -2 l2 6 l-10 1.5 Z" />
            <path className="g-sill" d="M152 197 H244" />
            <path className="g-tail" d="M87 162 L97 159" />
            <path className="g-head" d="M297 162 L312 168" />
            <circle className="g-port" cx="99" cy="168" r="3.4" />
            {plugged && <circle className="g-port-glow" cx="99" cy="168" r="16" fill={url("glow")} />}
            {WHEELS.map((x) => (
              <g key={x} className="g-wheel">
                <circle className="g-tyre" cx={x} cy={190} r={19} />
                <circle className="g-rim" cx={x} cy={190} r={13} />
                <path className="g-spoke" d={spokes(x, 190)} />
                <circle className="g-hub" cx={x} cy={190} r={3.5} />
              </g>
            ))}
            {soc != null && phase === "present" && (
              <g className="g-soc">
                <rect x="172" y="98" width="56" height="22" rx="11" />
                <text x="200" y="113.5" textAnchor="middle">{Math.round(soc)} %</text>
              </g>
            )}
          </g>
        </g>
      )}
    </svg>
  );
}
