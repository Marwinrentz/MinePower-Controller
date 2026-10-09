/** Zahlen- und Zeitformatierung.
 *
 *  Deutsch ist die Leitsprache: Dezimalkomma, 'kW" mit Leerzeichen, 24-h-Zeit.
 *  `setFormatLocale` stellt auf Englisch um, wenn die Oberfläche englisch ist
 *  (siehe i18n/index.tsx).
 */

let locale = "de-DE";

export function setFormatLocale(lang: string): void {
  locale = lang === "en" ? "en-GB" : "de-DE";
}

const cache = new Map<string, Intl.NumberFormat>();
function nf(digits: number): Intl.NumberFormat {
  const key = `${locale}:${digits}`;
  let f = cache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    cache.set(key, f);
  }
  return f;
}

export function fmtNum(value: number, digits = 0): string {
  return nf(digits).format(value);
}

/** Leistung: unter 1 kW in Watt, darüber in kW mit sinnvoller Stellenzahl. */
export function fmtW(watts: number | null | undefined): string {
  if (watts === null || watts === undefined || !Number.isFinite(watts)) return "–";
  const abs = Math.abs(watts);
  if (abs >= 10000) return `${fmtNum(watts / 1000, 1)} kW`;
  if (abs >= 1000) return `${fmtNum(watts / 1000, 2)} kW`;
  return `${fmtNum(Math.round(watts))} W`;
}

export function fmtKwh(kwh: number | null | undefined, digits = 1): string {
  if (kwh === null || kwh === undefined || !Number.isFinite(kwh)) return "–";
  return `${fmtNum(kwh, digits)} kWh`;
}

export function fmtCt(ct: number | null | undefined, digits = 1): string {
  if (ct === null || ct === undefined || !Number.isFinite(ct)) return "–";
  return `${fmtNum(ct, digits)} ct`;
}

export function fmtPct(pct: number | null | undefined): string {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return "–";
  return `${fmtNum(Math.round(pct))} %`;
}

export function fmtTemp(c: number | null | undefined, digits = 0): string {
  if (c === null || c === undefined || !Number.isFinite(c)) return "–";
  return `${fmtNum(c, digits)} °C`;
}

/** Dauer aus Sekunden: '12 min", '1 h 05 min". */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  const m = Math.max(0, Math.round(seconds / 60));
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString(locale, {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

export function fmtClock(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

export function fmtDay(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toLocaleDateString(locale, { weekday: "short", day: "2-digit", month: "2-digit" });
}

/** 'vor 40 s", 'vor 3 min", 'vor 2 h" – für das Alter eines Messwerts. */
export function fmtAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "–";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  const de = locale.startsWith("de");
  if (s < 60) return de ? `vor ${s} s` : `${s} s ago`;
  if (s < 3600) return de ? `vor ${Math.round(s / 60)} min` : `${Math.round(s / 60)} min ago`;
  return de ? `vor ${Math.round(s / 3600)} h` : `${Math.round(s / 3600)} h ago`;
}

/** CSS-Variable zur Laufzeit lesen (für ECharts-Farben). */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Kalenderdatum aus 'YYYY-MM-DD" (ohne Zeitzonenverschiebung). */
export function fmtDate(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  if (!y || !m || !d) return isoDate;
  return new Date(y, m - 1, d).toLocaleDateString(locale, { day: "2-digit", month: "2-digit", year: "numeric" });
}
