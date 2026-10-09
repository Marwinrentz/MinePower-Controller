/** Beschriftungen an einer Skala so verschieben, dass sie sich nicht
 *  überlappen (mindestens `gap` Einheiten Abstand), ohne die Reihenfolge zu
 *  ändern. Die Markierungslinie bleibt am echten Wert, nur der Text rückt. */
export function spreadLabels(ys: number[], gap: number): number[] {
  const order = ys.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y);
  const out = new Array<number>(ys.length);
  let last = -Infinity;
  for (const { y, i } of order) {
    const placed = Math.max(y, last + gap);
    out[i] = placed;
    last = placed;
  }
  return out;
}
