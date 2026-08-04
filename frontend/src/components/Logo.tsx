/** Die Wortmarke als echte Vektorform statt Emoji-Platzhalter.
 *
 *  Inline statt <img src="/logo.svg">, damit das Zeichen die Textfarbe der
 *  Umgebung nicht braucht, sofort ohne zweiten Netzabruf steht und in der
 *  Kopfzeile pixelgenau zur Schriftgröße passt.
 *
 *  Die Verlaufs-ID kommt aus useId: Sind mehrere Logos gleichzeitig im
 *  Dokument (Seitenleiste und Kopfzeile), würden gleiche IDs kollidieren.
 */
import { useId } from "react";

export function Logo({ size = 24 }: { size?: number }) {
  const id = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" role="img" aria-label="MinePower">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#22c55e" />
          <stop offset="1" stopColor="#0ea5b7" />
        </linearGradient>
      </defs>
      <rect width="100" height="100" rx="22.5" fill={`url(#${id})`} />
      <g fill="none" stroke="#fff" strokeWidth="5.2" strokeLinecap="round">
        <line x1="57.00" y1="33.90" x2="69.00" y2="33.90" />
        <line x1="54.95" y1="38.85" x2="63.44" y2="47.34" />
        <line x1="45.05" y1="38.85" x2="36.56" y2="47.34" />
        <line x1="43.00" y1="33.90" x2="31.00" y2="33.90" />
        <line x1="45.05" y1="28.95" x2="36.56" y2="20.46" />
        <line x1="50.00" y1="26.90" x2="50.00" y2="14.90" />
        <line x1="54.95" y1="28.95" x2="63.44" y2="20.46" />
      </g>
      <circle cx="50.0" cy="33.9" r="10.7" fill="#fff" />
      <polygon points="56.3,45.0 41.0,68.3 47.8,68.3 43.7,88.0 59.0,62.9 52.3,62.9" fill="#fff" />
    </svg>
  );
}
