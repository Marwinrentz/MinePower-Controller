/** Wärmelasten (Kategorie water_heater): Heizstab, Wärmepumpe, Klimaanlage,
 *  Heizung. Backend-Kategorie bleibt eine – Art, Symbol, Farbe und Texte
 *  kommen von hier, damit Übersicht, Energiefluss, Wand und Wärme-Seite
 *  dasselbe zeigen. */
import type { IconName } from "../components/icons";
import type { Device, SnapshotDevice } from "./types";

export type HeatKind = "water" | "heat_pump" | "climate" | "heating";

export function heatKind(dev: Pick<SnapshotDevice, "kind" | "capabilities"> | undefined | null): HeatKind {
  const k = dev?.kind;
  if (k === "heat_pump" || k === "climate" || k === "heating" || k === "water") return k;
  const caps = dev?.capabilities ?? [];
  if (caps.includes("heat_pump")) return "heat_pump";
  if (caps.includes("climate")) return "climate";
  return "water";
}

export const HEAT_ICON: Record<HeatKind, IconName> = {
  water: "water", heat_pump: "heatpump", climate: "snow", heating: "flame",
};

/** CSS-Ton (var(--c-…)) je Art */
export const HEAT_TONE: Record<HeatKind, string> = {
  water: "water", heat_pump: "heatpump", climate: "climate", heating: "heatpump",
};

/** i18n-Schlüssel für den Namen der Art */
export const HEAT_LABEL: Record<HeatKind, string> = {
  water: "heat.kindWater", heat_pump: "heat.kindHeatPump", climate: "heat.kindClimate", heating: "heat.kindHeating",
};

export function isHeat(dev: SnapshotDevice): boolean {
  return dev.category === "water_heater";
}

export function devPower(dev: SnapshotDevice | undefined | null): number {
  const p = dev?.data?.power;
  return typeof p === "number" && Number.isFinite(p) ? p : 0;
}

export function devTemp(dev: SnapshotDevice | undefined | null): number | null {
  const t = dev?.data?.temperature_c;
  return typeof t === "number" && Number.isFinite(t) ? t : null;
}

/** Farbe für eine Geräte-Linie im Verlauf: Kategorie-Farbe, ab dem zweiten
 *  Gerät derselben Art leicht abgewandelt, damit Linien unterscheidbar
 *  bleiben. */
export function seriesTone(dev: SnapshotDevice): string {
  return dev.category === "wallbox" ? "car" : HEAT_TONE[heatKind(dev)];
}

/** Art eines eingerichteten Geräts (Einstellungen, Reihenfolge) – ohne Live-Daten. */
export function configuredKind(d: Pick<Device, "category" | "driver_id" | "config" | "settings">): HeatKind | null {
  if (d.category !== "water_heater") return null;
  const k = (d.settings?.kind ?? d.config?.heat_kind) as string | undefined;
  if (k === "heat_pump" || k === "climate" || k === "heating" || k === "water") return k;
  if (/sgready|viessmann|heat_pump|homey/.test(d.driver_id)) return "heat_pump";
  if (/daikin/.test(d.driver_id)) return "climate";
  return "water";
}

/** Symbol für ein eingerichtetes Gerät (Wärme je Art, sonst Kategorie). */
export function deviceIcon(d: Pick<Device, "category" | "driver_id" | "config" | "settings">, fallback: IconName): IconName {
  const k = configuredKind(d);
  return k ? HEAT_ICON[k] : fallback;
}
