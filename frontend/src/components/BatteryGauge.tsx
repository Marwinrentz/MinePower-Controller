/** Batterie als waagerechter Füllstand.
 *
 *  Breite Zelle mit Pol rechts, Füllung bis zum Ladestand, Markierungen für
 *  Reserve und 'Batterie zuerst bis'. Laden und Entladen zeigt ein langsam
 *  wanderndes Streifenmuster in Flussrichtung (prefers-reduced-motion: aus).
 *  Netzladen färbt die Füllung blau. Waagerecht passt die Grafik ohne
 *  Leerraum auf Handy und Desktop.
 */
export type GaugeMark = { soc: number; label: string; kind: "reserve" | "priority" | "inverter" };

const clamp = (v: number) => Math.max(0, Math.min(100, v));

/** Beschriftungen, die sich überdecken würden, auseinanderschieben (in %). */
function spread(positions: number[], gap = 18): number[] {
  const order = positions.map((p, i) => [p, i] as const).sort((a, b) => a[0] - b[0]);
  const out = [...positions];
  let last = -Infinity;
  for (const [p, i] of order) {
    const v = Math.max(p, last + gap);
    out[i] = Math.min(v, 100);
    last = out[i];
  }
  return out;
}

export function BatteryGauge({ soc, power, gridCharging, marks, label }: {
  soc: number | null; power: number; gridCharging?: boolean; marks: GaugeMark[]; label: string;
}) {
  const flow = power > 30 ? "in" : power < -30 ? "out" : "idle";
  const level = soc == null ? 0 : clamp(soc);
  const shown = marks.filter((m) => m.kind !== "inverter").map((m) => ({ ...m, soc: clamp(m.soc) }));
  const labelPos = spread(shown.map((m) => m.soc));
  return (
    <div className={`bgauge flow-${flow} ${gridCharging ? "grid" : ""}`} role="img"
         aria-label={`${label}: ${soc != null ? Math.round(soc) : "–"} %`}>
      <div className="bg-cell">
        <div className="bg-fill" style={{ width: `${level}%` }}>
          <span className="bg-stripes" aria-hidden="true" />
        </div>
        {shown.map((m) => (
          <span key={m.kind} className={`bg-mark ${m.kind}`} style={{ left: `${m.soc}%` }} aria-hidden="true" />
        ))}
      </div>
      <span className="bg-pole" aria-hidden="true" />
      <div className="bg-labels" aria-hidden="true">
        {shown.map((m, i) => (
          <span key={m.kind} style={{ left: `${labelPos[i]}%` }}
                className={`bg-label ${m.kind} ${labelPos[i] < 12 ? "start" : labelPos[i] > 88 ? "end" : ""}`}>
            {m.label}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Restzeit bis voll (Laden) bzw. bis zur Reserve (Entladen); null ohne Kapazität. */
export function batteryEta(soc: number | null, power: number, capacityKwh: number | null | undefined,
                           reserve: number): { hours: number; to: "full" | "reserve" } | null {
  if (soc == null || !capacityKwh || Math.abs(power) < 50) return null;
  if (power > 0) {
    if (soc >= 99.5) return null;
    return { hours: ((100 - soc) / 100) * capacityKwh * 1000 / power, to: "full" };
  }
  if (soc <= reserve) return null;
  return { hours: ((soc - reserve) / 100) * capacityKwh * 1000 / -power, to: "reserve" };
}
