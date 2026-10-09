/** Netzpunkt-Waage und Verteilungsleiste – die 'Auf-einen-Blick"-Ansicht.
 *
 *  Die Kernfrage einer Überschussregelung ist **nicht** 'wie viel läuft wo?",
 *  sondern: *Stehe ich am Netzpunkt auf null?* Genau das beantwortet die
 *  Waage oben, und zwar ohne dass man eine Zahl lesen muss:
 *
 *      Einspeisung ────────────┼──────────── Bezug
 *                          ▓▓▓▓▓│
 *                        └── Zielband ──┘
 *
 *  • Die Mitte ist 0 W. Der Balken wächst aus der Mitte nach links
 *    (Einspeisung) oder rechts (Bezug) – nie 'irgendwo im Nichts".
 *  • Um die Mitte liegt das **Zielband**: das tatsächlich wirksame Totband
 *    der Regelung (adaptiv, ändert sich mit dem Wetter). Liegt der Balken
 *    darin, regelt die Anlage sauber – und die Waage wird grün.
 *  • Steht der Balken links außerhalb, geht Überschuss ins Netz verloren.
 *    Das ist derselbe Wert wie die Kennzahl 'verschenkt".
 *
 *  Damit sieht man aus mehreren Metern Entfernung nicht nur *einen Messwert*,
 *  sondern ob er **innerhalb der Toleranz** liegt – die Information, auf die
 *  es tatsächlich ankommt. Eine gestapelte Mengenleiste allein kann das
 *  prinzipiell nicht zeigen, weil ihr der Bezugspunkt fehlt.
 *
 *  Darunter die Verteilungsleiste: Woher kommt die Energie, wohin geht sie.
 *  Beide Zeilen auf identischer Skala, damit man Blöcke untereinander
 *  vergleichen kann.
 */
import { fmtW } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { useAnimatedNumber } from "./ui";
import { Icon, type IconName } from "./icons";

/* ------------------------------------------------------------------ Waage */

/** Feste Skalenstufen: Der Balken soll bei jedem Messwert-Update nicht die
 *  Skala wechseln – sonst 'atmet" die ganze Anzeige und wird unlesbar. */
const SCALE_STEPS = [1000, 2000, 3000, 5000, 8000, 12000, 20000, 30000];

function pickScale(watts: number): number {
  const need = Math.abs(watts) * 1.2;
  return SCALE_STEPS.find((s) => s >= need) ?? SCALE_STEPS[SCALE_STEPS.length - 1];
}

export function BalanceMeter({ snapshot, t, size = "md" }: {
  snapshot: Snapshot; t: (k: string) => string; size?: "sm" | "md" | "lg";
}) {
  const grid = snapshot.grid_power ?? 0;
  const animated = useAnimatedNumber(grid);
  const scale = pickScale(grid);
  const band = Math.max(snapshot.deadband_w || 0, 50);

  // Position in Prozent der halben Breite, aus der Mitte heraus
  const offset = Math.max(-1, Math.min(1, animated / scale));
  const bandShare = Math.min(0.5, band / scale);

  const inBand = Math.abs(grid) <= band;
  const importing = grid > band;
  const tone = inBand ? "ok" : importing ? "import" : "export";

  const label = inBand
    ? t("dash.balancePerfect")
    : importing
      ? t("dash.import")
      : t("dash.export");

  return (
    <div className={`bal bal-${size} bal-${tone}`}>
      <div className="bal-top">
        <span className="bal-caption">{t("dash.balanceTitle")}</span>
        <span className="bal-state">{label}</span>
      </div>

      <div className="bal-readout">
        <span className="bal-value num">{inBand ? "0" : fmtW(Math.abs(animated))}</span>
        {inBand && <span className="bal-unit">W</span>}
      </div>

      <div className="bal-track" role="img"
           aria-label={`${label}: ${fmtW(Math.abs(grid))}`}>
        {/* Zielband um die Mitte – die sichtbare Toleranz der Regelung */}
        <div className="bal-band"
             style={{ left: `${50 - bandShare * 50}%`, width: `${bandShare * 100}%` }} />
        {/* Nulllinie */}
        <div className="bal-zero" />
        {/* Ausschlag aus der Mitte */}
        <div
          className="bal-fill"
          style={
            offset >= 0
              ? { left: "50%", width: `${offset * 50}%` }
              : { right: "50%", width: `${-offset * 50}%` }
          }
        />
      </div>

      <div className="bal-scale">
        <span>{t("dash.export")}</span>
        <span className="bal-scale-mid">0</span>
        <span>{t("dash.import")}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------ Verteilung (zwei Zeilen) */

type Seg = { key: string; label: string; icon: IconName; watts: number; color: string };

const MIN_SHARE = 0.05;

function buildSegments(s: Snapshot, t: (k: string) => string) {
  const grid = s.grid_power ?? 0;
  const bat = s.battery?.power ?? 0;

  const sources: Seg[] = [
    { key: "pv", label: t("dash.pv"), icon: "sun", watts: s.pv_power, color: "var(--c-pv)" },
    { key: "imp", label: t("dash.import"), icon: "grid", watts: Math.max(0, grid), color: "var(--c-grid-import)" },
    { key: "bout", label: t("dash.battery"), icon: "battery", watts: Math.max(0, -bat), color: "var(--c-battery)" },
  ];
  const loads: Seg[] = [
    { key: "house", label: t("dash.house"), icon: "house", watts: s.house_power, color: "var(--c-house)" },
    { key: "car", label: t("dash.car"), icon: "car", watts: s.wallbox_power, color: "var(--c-car)" },
    { key: "water", label: t("dash.water"), icon: "water", watts: s.water_power, color: "var(--c-water)" },
    { key: "bin", label: t("dash.battery"), icon: "battery", watts: Math.max(0, bat), color: "var(--c-battery)" },
    { key: "exp", label: t("dash.export"), icon: "export", watts: Math.max(0, -grid), color: "var(--c-grid-export)" },
  ];
  return { sources: sources.filter((x) => x.watts > 20), loads: loads.filter((x) => x.watts > 20) };
}

function BarRow({ items, scale, label }: { items: Seg[]; scale: number; label: string }) {
  return (
    <div className="fbar-line">
      <span className="fbar-tag">{label}</span>
      <div className="fbar-track">
        {items.length === 0 && <div className="fbar-empty" />}
        {items.map((seg) => {
          const share = seg.watts / scale;
          const width = Math.max(share, MIN_SHARE) * 100;
          return (
            <div key={seg.key} className="fbar-seg"
                 style={{ width: `${width}%`, background: seg.color }}
                 title={`${seg.label}: ${fmtW(seg.watts)}`}>
              <span className="fbar-seg-inner">
                <Icon name={seg.icon} size={14} />
                {share > 0.14 && <span className="num">{fmtW(seg.watts)}</span>}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function FlowBar({ snapshot, t }: { snapshot: Snapshot; t: (k: string) => string }) {
  const { sources, loads } = buildSegments(snapshot, t);
  const total = Math.max(
    sources.reduce((a, s) => a + s.watts, 0),
    loads.reduce((a, s) => a + s.watts, 0),
    1,
  );
  return (
    <div className="fbar">
      <BarRow items={sources} scale={total} label={t("dash.barSources")} />
      <BarRow items={loads} scale={total} label={t("dash.barLoads")} />
    </div>
  );
}


/* ------------------------------------------------ Wand: Balken je Fluss */

type Row = { key: string; label: string; icon: IconName; watts: number; color: string; note?: string };

/** Ein Balken je Energiefluss, alle auf derselben, festen Skala.
 *
 *  Aus drei Metern gelesen: Symbol und Name links groß, der Balken in der
 *  Farbe der Energieart, die Zahl rechts groß. Keine Beschriftung im Balken
 *  (die wird bei kleinen Werten unlesbar), keine Mini-Segmente. Die Skala
 *  springt nur in festen Stufen, damit die Anzeige nicht 'atmet". */
export function WallBars({ snapshot, t }: { snapshot: Snapshot; t: (k: string) => string }) {
  const grid = snapshot.grid_power ?? 0;
  const bat = snapshot.battery?.power ?? 0;
  const rows: Row[] = [
    { key: "pv", label: t("dash.pv"), icon: "sun", watts: snapshot.pv_power, color: "var(--c-pv)" },
    { key: "house", label: t("dash.house"), icon: "house", watts: snapshot.house_power, color: "var(--c-house)" },
    { key: "car", label: t("dash.car"), icon: "car", watts: snapshot.wallbox_power, color: "var(--c-car)" },
    { key: "water", label: t("dash.water"), icon: "water", watts: snapshot.water_power, color: "var(--c-water)" },
  ];
  if (snapshot.battery) {
    rows.push({
      key: "bat", label: t("dash.battery"), icon: "battery", watts: Math.abs(bat), color: "var(--c-battery)",
      note: bat > 30 ? t("dash.charging") : bat < -30 ? t("dash.discharging") : undefined,
    });
  }
  rows.push({
    key: "grid", label: t("dash.grid"), icon: "grid", watts: Math.abs(grid),
    color: grid > 0 ? "var(--c-grid-import)" : "var(--c-grid-export)",
    note: grid > 50 ? t("dash.import") : grid < -50 ? t("dash.export") : undefined,
  });
  const scale = pickScale(Math.max(...rows.map((r) => r.watts), 1000) / 1.2);

  return (
    <div className="wbars" role="list">
      {rows.map((r) => <WallBar key={r.key} row={r} scale={scale} />)}
      <div className="wbars-scale" aria-hidden="true">
        <span>0</span>
        <span>{fmtW(scale / 2)}</span>
        <span>{fmtW(scale)}</span>
      </div>
    </div>
  );
}

function WallBar({ row, scale }: { row: Row; scale: number }) {
  const animated = useAnimatedNumber(row.watts);
  const share = Math.max(0, Math.min(1, animated / scale));
  const idle = row.watts < 50;
  return (
    <div className={`wbar ${idle ? "idle" : ""}`} role="listitem">
      <span className="wbar-label"><Icon name={row.icon} size={26} /> {row.label}</span>
      <div className="wbar-track">
        <div className="wbar-fill" style={{ width: `${share * 100}%`, background: row.color }} />
        <div className="wbar-mid" />
      </div>
      <span className="wbar-value num" style={{ color: idle ? undefined : row.color }}>
        {fmtW(animated)}
        {row.note && <small>{row.note}</small>}
      </span>
    </div>
  );
}
