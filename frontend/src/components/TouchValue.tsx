/** Wert einstellen am Touchscreen – Ersatz für den freien Schieberegler.
 *
 *  Warum kein `<input type="range">` mehr am Handy: Chrome setzt den Wert
 *  schon beim Berühren der Spur (touchstart), bevor klar ist, ob der Finger
 *  scrollen will. Der alte Regler schickte den Wert dann bei touchend/blur
 *  ab – wer über eine Karte mit Regler scrollte, änderte nebenbei die
 *  Zieltemperatur oder das Ladelimit.
 *
 *  Stattdessen:
 *  • **Stepper −/+**: reagieren nur auf einen echten Tipp (click), nie auf
 *    eine Wischbewegung. Gedrückt halten wiederholt, bis sich der Finger
 *    bewegt (dann scrollt die Seite und die Wiederholung bricht ab).
 *  • **Blatt mit Regler**: Tipp auf die Wertleiste öffnet ein Blatt mit
 *    Voreinstellungen und einem Regler, der erst nach einer eindeutig
 *    waagerechten Bewegung (Schwelle 10 px) greift. Senkrecht gewinnt
 *    immer das Scrollen. Gesendet wird erst beim Loslassen.
 *  • **Rückgängig**: Jede gespeicherte Änderung meldet sich mit einem
 *    'Rückgängig"-Knopf.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { tr, useI18n } from "../i18n";
import { GESTURE_SLOP, gestureAxis, haptic } from "../lib/touch";
import { toast } from "../store/toast";
import { Icon } from "./icons";
import { Sheet } from "./Sheet";

/** Stepper-Änderungen werden gesammelt und nach dieser Pause gesendet. */
const COMMIT_IDLE_MS = 900;
const REPEAT_DELAY_MS = 450;
const REPEAT_EVERY_MS = 110;

export type TouchValueProps = {
  value: number; min: number; max: number; step?: number;
  onChange?: (v: number) => void; onCommit?: (v: number) => void;
  format?: (v: number) => string; label?: string; help?: ReactNode;
  marks?: { value: number; label: string }[]; disabled?: boolean;
  tone?: string; id?: string; offLabel?: string;
};

const clamp = (v: number, min: number, max: number, step: number) => {
  const snapped = Math.round((v - min) / step) * step + min;
  return Math.max(min, Math.min(max, Number(snapped.toFixed(4))));
};

function presetsFor(min: number, max: number, step: number, marks?: { value: number; label: string }[]) {
  if (marks && marks.length >= 3) return marks.map((m) => m.value);
  const out = new Set<number>();
  for (let i = 0; i <= 4; i++) out.add(clamp(min + ((max - min) * i) / 4, min, max, step));
  return [...out];
}

export function TouchValue(props: TouchValueProps) {
  const { value, min, max, step = 1, onChange, onCommit, format, label: given, help, marks, disabled, tone, offLabel } = props;
  const { t } = useI18n();
  const label = given ?? t("touch.value");
  const [draft, setDraft] = useState<number | null>(null);
  const [sheet, setSheet] = useState(false);
  const shown = draft ?? value;
  const text = (v: number) => (offLabel && v <= min ? offLabel : format ? format(v) : String(v));
  const pct = max > min ? ((shown - min) / (max - min)) * 100 : 0;
  const timer = useRef<number>(0);
  const before = useRef<number | null>(null);

  // Bestätigt der Live-Wert den Entwurf, ist er erledigt.
  useEffect(() => { if (draft !== null && draft === value && !timer.current) setDraft(null); }, [value, draft]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  /** Endgültig übernehmen (senden bzw. dem Formular melden). */
  const commit = (v: number, from: number) => {
    window.clearTimeout(timer.current);
    timer.current = 0;
    if (v === from) { setDraft(null); return; }
    if (onCommit) {
      onCommit(v);
      haptic(10);
      toast.undo(`${label}: ${text(v)}`, () => {
        setDraft(from);
        onCommit(from);
        haptic([6, 30, 6]);
      });
    } else {
      onChange?.(v);
    }
  };

  /** Wert ändern; ohne `onCommit` (Formular mit Speicherleiste) sofort. */
  const change = (v: number, immediate = false) => {
    const next = clamp(v, min, max, step);
    if (before.current === null) before.current = value;
    setDraft(next);
    if (!onCommit) {
      onChange?.(next);
      before.current = null;
      return;
    }
    onChange?.(next);
    window.clearTimeout(timer.current);
    const from = before.current;
    const done = () => { before.current = null; commit(next, from); };
    if (immediate) done();
    else timer.current = window.setTimeout(done, COMMIT_IDLE_MS);
  };

  return (
    <div className={`tval tone-${tone ?? "accent"} ${disabled ? "disabled" : ""}`}>
      {given && (
        <div className="tval-head">
          <span className="tval-label">{label}</span>
          <output className="tval-value num" aria-live="polite">{text(shown)}</output>
        </div>
      )}
      <div className="tval-row">
        <Stepper dir={-1} disabled={disabled || shown <= min} label={label}
                 onStep={() => change(shown - step)} />
        <button type="button" className="tval-bar" disabled={disabled} onClick={() => { haptic(6); setSheet(true); }}
                aria-label={t("touch.change", { label, value: text(shown) })}>
          <span className="tval-track"><span className="tval-fill" style={{ transform: `scaleX(${pct / 100})` }} /></span>
          <span className="tval-dot" style={{ left: `${pct}%` }} />
        </button>
        <Stepper dir={1} disabled={disabled || shown >= max} label={label}
                 onStep={() => change(shown + step)} />
      </div>
      {help && <div className="field-help">{help}</div>}

      <Sheet open={sheet} onClose={() => setSheet(false)} title={label}
             footer={<button type="button" className="mbtn primary" onClick={() => setSheet(false)}>{t("common.done")}</button>}>
        <div className={`tval-sheet tone-${tone ?? "accent"}`}>
          <output className="tval-big num">{text(shown)}</output>
          <TouchRange value={shown} min={min} max={max} step={step} label={label} valueText={text(shown)}
                      onRelease={(v) => change(v, true)} />
          <div className="tval-sheet-steps">
            <Stepper dir={-1} big disabled={shown <= min} label={label} onStep={() => change(shown - step)} />
            <Stepper dir={1} big disabled={shown >= max} label={label} onStep={() => change(shown + step)} />
          </div>
          <div className="tval-presets" role="group" aria-label={t("touch.presets")}>
            {presetsFor(min, max, step, marks).map((p) => (
              <button key={p} type="button" className={`tval-preset ${p === shown ? "on" : ""}`}
                      aria-pressed={p === shown} onClick={() => change(p, true)}>
                {text(p)}
              </button>
            ))}
          </div>
        </div>
      </Sheet>
    </div>
  );
}

/** −/+ mit Wiederholung beim Halten. Löst nur per Tipp aus (click), nicht
 *  bei pointerdown – eine Wischbewegung über den Knopf bewirkt nichts. */
function Stepper({ dir, onStep, disabled, label, big = false }: {
  dir: -1 | 1; onStep: () => void; disabled?: boolean; label?: string; big?: boolean;
}) {
  const repeat = useRef<{ delay: number; every: number; fired: boolean; x: number; y: number } | null>(null);
  const stop = () => {
    const r = repeat.current;
    if (r) { window.clearTimeout(r.delay); window.clearInterval(r.every); }
    repeat.current = null;
  };
  useEffect(() => stop, []);
  const latest = useRef(onStep);
  latest.current = onStep;

  return (
    <button
      type="button"
      className={`tval-step ${big ? "big" : ""}`}
      disabled={disabled}
      aria-label={tr(dir < 0 ? "touch.decrease" : "touch.increase", { label: label ?? tr("touch.value") })}
      onPointerDown={(e) => {
        stop();
        const r = { delay: 0, every: 0, fired: false, x: e.clientX, y: e.clientY };
        r.delay = window.setTimeout(() => {
          r.fired = true;
          latest.current();
          r.every = window.setInterval(() => latest.current(), REPEAT_EVERY_MS);
        }, REPEAT_DELAY_MS);
        repeat.current = r;
      }}
      onPointerMove={(e) => {
        const r = repeat.current;
        if (r && gestureAxis(e.clientX - r.x, e.clientY - r.y, GESTURE_SLOP) !== null) stop();
      }}
      onPointerUp={() => { const r = repeat.current; if (r && !r.fired) { window.clearTimeout(r.delay); } }}
      onPointerCancel={stop}
      onPointerLeave={stop}
      onClick={() => {
        const r = repeat.current;
        stop();
        if (r?.fired) return; // Wiederholung lief schon
        haptic(6);
        onStep();
      }}
    >
      <Icon name={dir < 0 ? "minus" : "plus"} size={big ? 26 : 22} />
    </button>
  );
}

/** Regler im Blatt: greift erst nach eindeutig waagerechter Bewegung,
 *  folgt dann relativ zum Finger (kein Sprung an die Tippstelle) und
 *  meldet den Wert erst beim Loslassen. Ein Tipp allein ändert nichts. */
export function TouchRange({ value, min, max, step, label, valueText, onRelease }: {
  value: number; min: number; max: number; step: number; label: string; valueText: string;
  onRelease: (v: number, from: number) => unknown;
}) {
  const track = useRef<HTMLDivElement>(null);
  const g = useRef<{ x: number; y: number; from: number; active: boolean; dead: boolean } | null>(null);
  const [live, setLive] = useState<number | null>(null);
  const shown = live ?? value;
  const pct = max > min ? ((shown - min) / (max - min)) * 100 : 0;

  const valueAt = (dx: number, from: number) => {
    const width = track.current?.getBoundingClientRect().width || 1;
    return clamp(from + (dx / width) * (max - min), min, max, step);
  };

  return (
    <div
      ref={track}
      className={`trange ${live !== null ? "active" : ""}`}
      role="slider"
      tabIndex={0}
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={shown}
      aria-valuetext={valueText}
      onKeyDown={(e) => {
        const d = e.key === "ArrowRight" || e.key === "ArrowUp" ? step : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -step : 0;
        if (!d) return;
        e.preventDefault();
        onRelease(clamp(value + d, min, max, step), value);
      }}
      onPointerDown={(e) => {
        g.current = { x: e.clientX, y: e.clientY, from: value, active: false, dead: false };
      }}
      onPointerMove={(e) => {
        const s = g.current;
        if (!s || s.dead) return;
        const dx = e.clientX - s.x, dy = e.clientY - s.y;
        if (!s.active) {
          const axis = gestureAxis(dx, dy);
          if (axis === null) return;
          if (axis === "y") { s.dead = true; return; } // Scrollen gewinnt
          s.active = true;
          s.x = e.clientX; // ab hier relativ, ohne Sprung um die Schwelle
          try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); } catch { /* Zeiger schon weg */ }
          haptic(4);
          setLive(s.from);
          return;
        }
        setLive(valueAt(dx, s.from));
      }}
      onPointerUp={(e) => {
        const s = g.current;
        g.current = null;
        if (s?.active) {
          const v = valueAt(e.clientX - s.x, s.from);
          setLive(null);
          onRelease(v, s.from);
        }
      }}
      onPointerCancel={() => { g.current = null; setLive(null); }}
    >
      <span className="trange-track"><span className="trange-fill" style={{ transform: `scaleX(${pct / 100})` }} /></span>
      <span className="trange-thumb" style={{ left: `${pct}%` }} />
      <span className="trange-hint">{tr("touch.dragHint")}</span>
    </div>
  );
}
