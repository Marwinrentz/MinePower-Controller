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
  | "moon" | "display" | "flask" | "bolt"
  | "chevron" | "info" | "price" | "pulse" | "more" | "flame" | "thermo" | "clock"
  | "plug" | "refresh" | "download" | "import" | "lock" | "garage" | "arrowDown" | "shield"
  | "plus" | "minus" | "github";

/** Pfaddaten im 24×24-Raster. Bewusst nur Striche, keine Flächen. */
const PATHS: Record<IconName, string> = {
  dashboard: "M3 13h8V3H3v10Zm0 8h8v-6H3v6Zm10 0h8V11h-8v10Zm0-18v6h8V3h-8Z",
  devices: "M7 3v6m10-6v6M5 9h14v4a7 7 0 0 1-14 0V9Zm7 11v3",
  statistics: "M4 20V10m5 10V4m5 16v-7m5 7V8",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z",
  events: "M4 6h16M4 12h16M4 18h10",

  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Zm0-14v2m0 14v2M3 12h2m14 0h2M5.6 5.6l1.4 1.4m10 10 1.4 1.4m0-12.8-1.4 1.4m-10 10-1.4 1.4",
  car: "M5 17a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm14 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm-12 -2h10M3 15v-3.5L5 7h14l2 4.5V15",
  water: "M12 3s6 6.4 6 10.4A6 6 0 0 1 6 13.4C6 9.4 12 3 12 3Z",
  battery: "M4.5 7h11A1.5 1.5 0 0 1 17 8.5v7a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 15.5v-7A1.5 1.5 0 0 1 4.5 7ZM20 10.5v3",
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
  chevron: "M9 6l6 6-6 6",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-10.5V16m0-8.5v.5",
  price: "M3.5 12.5V4.5a1 1 0 0 1 1-1h8l8 8a1.5 1.5 0 0 1 0 2.1l-6.9 6.9a1.5 1.5 0 0 1-2.1 0l-8-8ZM8 8h.01",
  pulse: "M3 12h4l3-7 4 14 3-7h4",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  flame: "M12 3c1 3.5 5 5.5 5 10a5 5 0 0 1-10 0c0-2.5 1.5-4 2.5-5 .3 1.6 1.2 2.5 2.5 3-1-3 0-6 0-8Z",
  thermo: "M10 13.5V5a2 2 0 1 1 4 0v8.5a4 4 0 1 1-4 0Z",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v4.5l3 2",
  plug: "M9 3v5m6-5v5M7 8h10v3a5 5 0 0 1-10 0V8Zm5 8v5",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  download: "M12 4v11m0 0-5-5m5 5 5-5M5 20h14",
  import: "M12 5v14m0 0-6-6m6 6 6-6",
  lock: "M6 11h12v9H6v-9Zm2 0V8a4 4 0 1 1 8 0v3",
  arrowDown: "M12 5v14m0 0 6-6m-6 6-6-6",
  shield: "M12 3 5 6v5c0 4.4 3 8.3 7 9.5 4-1.2 7-5.1 7-9.5V6l-7-3Z",
  garage: "M3 10 12 4l9 6v10H3V10Zm4 10v-7h10v7M7 16h10",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  github: "M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 4 2c-.3 1.15-.3 2.35 0 3.5A5.4 5.4 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4M9 18c-4.51 2-5-2-7-2",
};

/** Symbole, die eine Fläche brauchen statt einer Linie. */
const FILLED: ReadonlySet<IconName> = new Set<IconName>(["play", "stop", "dashboard"]);
/** Punkte: dicke Linie mit runden Enden ergibt runde Punkte. */
const BOLD: ReadonlySet<IconName> = new Set<IconName>(["more"]);

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
      strokeWidth={BOLD.has(name) ? 3 : strokeWidth}
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
