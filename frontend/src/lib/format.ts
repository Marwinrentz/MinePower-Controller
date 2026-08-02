/** Zahlenformatierung (de/en-neutral, kompakt). */

export function fmtW(watts: number | null | undefined): string {
  if (watts === null || watts === undefined) return "–";
  const abs = Math.abs(watts);
  if (abs >= 10000) return `${(watts / 1000).toFixed(1)} kW`;
  if (abs >= 1000) return `${(watts / 1000).toFixed(2)} kW`;
  return `${Math.round(watts)} W`;
}

export function fmtKwh(kwh: number | null | undefined): string {
  if (kwh === null || kwh === undefined) return "–";
  return `${kwh.toFixed(1)} kWh`;
}

export function fmtPct(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return "–";
  return `${Math.round(pct)} %`;
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/** CSS-Variable zur Laufzeit lesen (für ECharts-Farben). */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
