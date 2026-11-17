/** Strommix im Augenblick: Aus welchen Quellen kommt die Leistung gerade?
 *
 *  Gerechnet wird nur mit gemessenen Werten: Sonne, Batterie (nur beim
 *  Entladen) und Netz (nur beim Bezug). Fehlt der Netzwert oder liefert gerade
 *  keine Quelle, gibt es kein Ergebnis – die Anzeige bleibt dann leer, statt
 *  einen Anteil zu erfinden.
 */
import type { Snapshot } from "../lib/types";

export type Mix = { pv: number; battery: number; grid: number };
export const MIX_KEYS = ["pv", "battery", "grid"] as const;

/** Unterhalb dieser Summe gilt die Anlage als ruhend (W). */
const MIN_TOTAL_W = 20;

export function supplyMix(s: Pick<Snapshot, "pv_power" | "grid_power" | "battery">): Mix | null {
  if (s.grid_power === null || !Number.isFinite(s.grid_power) || !Number.isFinite(s.pv_power)) return null;
  const pv = Math.max(0, s.pv_power);
  const battery = s.battery ? Math.max(0, -s.battery.power) : 0;
  const grid = Math.max(0, s.grid_power);
  const total = pv + battery + grid;
  if (total < MIN_TOTAL_W) return null;
  return { pv: pv / total, battery: battery / total, grid: grid / total };
}
