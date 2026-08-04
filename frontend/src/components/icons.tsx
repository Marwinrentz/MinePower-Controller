/** Strichsymbole für Navigation, Geräte und Bedienelemente.
 *
 *  Emoji waren hier die falsche Wahl: Sie werden von jedem Betriebssystem
 *  anders gezeichnet (farbig, verspielt, unterschiedlich groß), ignorieren
 *  die Textfarbe und lassen sich nicht auf die Strichstärke der übrigen
 *  Oberfläche abstimmen. Ein Steuergerät für eine Hausinstallation sieht
 *  damit aus wie eine Chat-App.
 *
 *  Alle Symbole teilen dasselbe Raster (24×24), dieselbe Strichstärke und
 *  `currentColor` – sie nehmen also die Farbe ihrer Umgebung an und passen
 *  sich Hell/Dunkel ohne Zutun an.
 */

export type IconName =
  | "dashboard" | "devices" | "statistics" | "settings" | "events"
  | "sun" | "car" | "water" | "battery" | "grid" | "house" | "export"
  | "play" | "pause" | "stop" | "close" | "check" | "warning"
  | "fullscreen" | "power" | "auto" | "boost"
  | "moon" | "display" | "flask" | "bolt";

/** Pfaddaten im 24×24-Raster. Bewusst nur Striche, keine Flächen. */
const PATHS: Record<IconName, string> = {
  dashboard: "M3 13h8V3H3v10Zm0 8h8v-6H3v6Zm10 0h8V11h-8v10Zm0-18v6h8V3h-8Z",
  devices: "M7 3v6m10-6v6M5 9h14v4a7 7 0 0 1-14 0V9Zm7 11v3",
  statistics: "M4 20V10m5 10V4m5 16v-7m5 7V8",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8.4-3a8.4 8.4 0 0 0-.1-1.3l2-1.5-2-3.4-2.3 1a8.4 8.4 0 0 0-2.3-1.3L15.3 3h-4l-.4 2.5a8.4 8.4 0 0 0-2.3 1.3l-2.3-1-2 3.4 2 1.5a8.4 8.4 0 0 0 0 2.6l-2 1.5 2 3.4 2.3-1a8.4 8.4 0 0 0 2.3 1.3l.4 2.5h4l.4-2.5a8.4 8.4 0 0 0 2.3-1.3l2.3 1 2-3.4-2-1.5c.06-.43.1-.86.1-1.3Z",
  events: "M4 6h16M4 12h16M4 18h10",

  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Zm0-14v2m0 14v2M3 12h2m14 0h2M5.6 5.6l1.4 1.4m10 10 1.4 1.4m0-12.8-1.4 1.4m-10 10-1.4 1.4",
  car: "M5 17a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm14 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm-12 -2h10M3 15v-3.5L5 7h14l2 4.5V15",
  water: "M12 3s6 6.4 6 10.4A6 6 0 0 1 6 13.4C6 9.4 12 3 12 3Z",
  battery: "M3 8h14v8H3V8Zm14 3h3v2h-3M6 11v2m3-2v2",
  grid: "M12 2v19M5 21 12 2l7 19M8.5 9h7M7 14.5h10",
  house: "M4 11 12 4l8 7v9H4v-9Z",
  export: "M12 20V5m0 0-6 6m6-6 6 6",

  play: "M7 4v16l13-8L7 4Z",
  pause: "M8 4v16m8-16v16",
  stop: "M6 6h12v12H6V6Z",
  close: "M6 6l12 12M18 6 6 18",
  check: "M4 12.5 9.5 18 20 6.5",
  warning: "M12 3 2 20h20L12 3Zm0 6v6m0 3v.5",
  fullscreen: "M4 9V4h5M20 9V4h-5M4 15v5h5m11-5v5h-5",
  power: "M12 3v9m6-6.7a8 8 0 1 1-12 0",
  auto: "M20.5 12a8.5 8.5 0 1 1-2.5-6M20.5 2.5v4h-4",
  boost: "M6 18.5 12 12l6 6.5M6 11.5 12 5l6 6.5",
  moon: "M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z",
  display: "M3 5h18v11H3V5Zm6 16h6m-3-5v5",
  flask: "M9 3h6M10 3v6L5 19a1.5 1.5 0 0 0 1.3 2h11.4A1.5 1.5 0 0 0 19 19l-5-10V3",
  bolt: "M13 2 4 14h7l-1 8 9-12h-7l1-8Z",
};

/** Symbole, die eine Fläche brauchen statt einer Linie. */
const FILLED: ReadonlySet<IconName> = new Set<IconName>(["play", "stop", "dashboard"]);

export function Icon({
  name,
  size = 20,
  strokeWidth = 1.7,
  className,
  title,
}: {
  name: IconName;
  size?: number;
  strokeWidth?: number;
  className?: string;
  title?: string;
}) {
  const filled = FILLED.has(name);
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke={filled ? "none" : "currentColor"}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      focusable="false"
    >
      {title && <title>{title}</title>}
      <path d={PATHS[name]} />
    </svg>
  );
}

/** Symbol je Gerätekategorie – an einer Stelle festgelegt, damit Desktop,
 *  Handy und Wandanzeige nicht auseinanderlaufen. */
export const CATEGORY_ICON: Record<string, IconName> = {
  inverter: "sun",
  meter: "grid",
  wallbox: "car",
  water_heater: "water",
  battery: "battery",
};
