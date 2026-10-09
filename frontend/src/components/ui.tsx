/** UI-Bausteine des MinePower-Design-Systems.
 *
 *  Grundsätze:
 *  • Bedienziele mindestens 48 px hoch – am Handy mit dem Daumen, im
 *    Querformat, mit nassen Fingern in der Garage.
 *  • Jede Aktion zeigt sofort, dass sie angekommen ist (gedrückter Zustand,
 *    Lade-Kreisel am Knopf, Meldung bei Erfolg oder Fehler).
 *  • Erklärungen stehen sichtbar unter dem Feld, nicht in Tooltips: Ein
 *    Tooltip ist auf dem Touchscreen nicht erreichbar.
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useCoarsePointer } from "../lib/touch";
import { Icon, type IconName } from "./icons";
import { TouchValue } from "./TouchValue";

/* ------------------------------------------------------------------ Flächen */

export function Card({ children, className = "", style, onClick, id }: {
  children: ReactNode; className?: string; style?: React.CSSProperties; onClick?: () => void; id?: string;
}) {
  return <div id={id} className={`card ${className}`} style={style} onClick={onClick}>{children}</div>;
}

export function Panel({ children, className = "", style }: {
  children: ReactNode; className?: string; style?: React.CSSProperties;
}) {
  return <div className={`panel ${className}`} style={style}>{children}</div>;
}

export function CardHead({ title, sub, children }: { title: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="card-head">
      <div className="card-head-text">
        {typeof title === "string" ? <h2>{title}</h2> : title}
        {sub && <p className="card-sub">{sub}</p>}
      </div>
      {children && <div className="card-head-actions">{children}</div>}
    </div>
  );
}

/** Seitenkopf: Titel, ein Satz Erklärung, Aktionen rechts. */
export function PageHead({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div className="page-head-text">
        <h1 className="page-title">{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </header>
  );
}

/* ------------------------------------------------------------------ Aktionen */

export type ButtonSize = "sm" | "md" | "lg" | "xl";
export type ButtonVariant = "" | "primary" | "danger" | "ghost" | "soft";

export function Spinner({ size = 16 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-hidden="true" />;
}

export function Button({
  children, onClick, variant = "", size = "md", disabled = false, pressed = false, small = false,
  block = false, icon = false, type = "button", title, ariaLabel, loading = false, className = "",
}: {
  children: ReactNode; onClick?: () => void;
  variant?: ButtonVariant; size?: ButtonSize;
  disabled?: boolean; pressed?: boolean;
  /** Alt: entspricht size="sm" */
  small?: boolean; block?: boolean; icon?: boolean;
  type?: "button" | "submit"; title?: string; ariaLabel?: string; loading?: boolean; className?: string;
}) {
  const sz = small ? "sm" : size;
  const classes = ["btn", variant, `btn-${sz}`, pressed ? "pressed" : "", block ? "block" : "",
    icon ? "icon" : "", loading ? "loading" : "", className].filter(Boolean).join(" ");
  return (
    <button
      type={type}
      title={title}
      aria-label={ariaLabel ?? (icon ? title : undefined)}
      aria-pressed={pressed || undefined}
      aria-busy={loading || undefined}
      className={classes}
      onClick={onClick}
      disabled={disabled || loading}
    >
      {loading && <Spinner size={sz === "xl" ? 22 : 16} />}
      {children}
    </button>
  );
}

/** Knopf für eine asynchrone Aktion: zeigt den Lade-Kreisel, solange sie
 *  läuft, und nimmt in der Zeit keinen zweiten Druck an. */
export function ActionButton({ onClick, children, ...rest }: Omit<Parameters<typeof Button>[0], "onClick" | "loading"> & {
  onClick: () => Promise<unknown> | void;
}) {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await onClick();
    } finally {
      setBusy(false);
    }
  };
  return <Button {...rest} onClick={run} loading={busy}>{children}</Button>;
}

export function Toggle({ on, onChange, label, disabled = false, id }: {
  on: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean; id?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      className={`switch ${on ? "on" : ""}`}
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
    >
      <span className="switch-knob" />
    </button>
  );
}

/** Schalter mit Beschriftung links und Erklärung darunter – die ganze Zeile
 *  ist klickbar (großes Bedienziel). */
export function ToggleRow({ label, help, on, onChange, disabled, children }: {
  label: string; help?: ReactNode; on: boolean; onChange: (v: boolean) => void; disabled?: boolean; children?: ReactNode;
}) {
  const id = useId();
  return (
    <div className={`toggle-row ${disabled ? "disabled" : ""}`}>
      <div className="toggle-row-main">
        <label htmlFor={id} className="toggle-row-label">{label}</label>
        <Toggle id={id} on={on} onChange={onChange} disabled={disabled} label={label} />
      </div>
      {help && <div className="field-help">{help}</div>}
      {children}
    </div>
  );
}

export type SegmentOption<T extends string> = {
  value: T; label: string; title?: string; icon?: IconName; hint?: string;
};

/** Segment-Auswahl. `size="lg"` füllt die Breite und gibt jedem Feld mindestens
 *  48 px Höhe; bei zu wenig Platz bricht sie in Zeilen um statt abzuschneiden. */
export function Segment<T extends string>({ options, value, onChange, ariaLabel, size = "md", pending }: {
  options: SegmentOption<T>[];
  value: T; onChange: (v: T) => void; ariaLabel?: string; size?: "md" | "lg";
  /** Wert, der gerade auf Bestätigung wartet (zeigt einen Lade-Punkt). */
  pending?: T | null;
}) {
  return (
    <div className={`segment segment-${size} ${size === "lg" && options.length <= 3 ? "segment-few" : ""}`} role="radiogroup" aria-label={ariaLabel}
         style={size === "lg" ? { ["--seg-count" as string]: options.length } : undefined}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          title={o.title}
          aria-checked={o.value === value}
          className={o.value === value ? "active" : ""}
          onClick={() => o.value !== value && onChange(o.value)}
        >
          {o.icon && <Icon name={o.icon} size={size === "lg" ? 18 : 15} />}
          <span className="segment-label">{o.label}</span>
          {o.hint && <span className="segment-hint">{o.hint}</span>}
          {pending === o.value && <span className="segment-pending" aria-hidden="true" />}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ Formular */

/** Tooltip – nur noch für Zusatzinformationen am Desktop (Statistik). */
export function Help({ text }: { text: string }) {
  return (
    <span className="tip" tabIndex={0}>
      <span className="tip-icon" aria-hidden="true">i</span>
      <span className="tip-text" role="tooltip">{text}</span>
    </span>
  );
}

export function Field({ label, help, children, hint, error, htmlFor }: {
  label: string; help?: ReactNode | null; children: ReactNode; hint?: string; error?: string | null; htmlFor?: string;
}) {
  return (
    <div className={`field ${error ? "has-error" : ""}`}>
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {error ? <div className="field-error" role="alert">{error}</div> : hint ? <small>{hint}</small> : null}
      {help ? <div className="field-help">{help}</div> : null}
    </div>
  );
}

/** Zahlenfeld mit Einheit dahinter. Leere Eingabe bleibt leer (statt 0). */
export function NumberInput({ value, onChange, min, max, step = 1, unit, id, placeholder, disabled }: {
  value: number | null | undefined; onChange: (v: number | null) => void;
  min?: number; max?: number; step?: number; unit?: string; id?: string; placeholder?: string; disabled?: boolean;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value === null || value === undefined || Number.isNaN(value) ? "" : String(value));
  return (
    <div className="input-unit">
      <input
        id={id}
        className="input num"
        type="number"
        inputMode="decimal"
        min={min}
        max={max}
        step={step}
        value={shown}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => {
          setDraft(e.target.value);
          const n = e.target.value === "" ? null : Number(e.target.value.replace(",", "."));
          if (n === null || Number.isFinite(n)) onChange(n);
        }}
        onBlur={() => setDraft(null)}
      />
      {unit && <span className="input-unit-label">{unit}</span>}
    </div>
  );
}

/** Schieberegler mit großem Griff und Live-Wert.
 *
 *  `onChange` folgt dem Finger, `onCommit` kommt einmal beim Loslassen – so
 *  sieht man den Wert sofort, gespeichert bzw. gesendet wird aber nicht bei
 *  jedem Pixel (ein Fahrzeug-Befehl je Bewegung würde den BLE-Proxy fluten). */
type SliderProps = {
  value: number; min: number; max: number; step?: number;
  onChange?: (v: number) => void; onCommit?: (v: number) => void;
  format?: (v: number) => string; label?: string; help?: ReactNode;
  marks?: { value: number; label: string }[]; disabled?: boolean;
  tone?: "accent" | "car" | "water" | "battery" | "pv" | "warn"; id?: string;
  /** Beschriftung für den kleinsten Wert, wenn er 'aus" bedeutet. */
  offLabel?: string;
};

/** Am Touchscreen: Stepper + Blatt statt freiem Regler (siehe TouchValue –
 *  der native Regler änderte beim Drüberscrollen Werte). Mit Maus der
 *  bisherige Regler. */
export function Slider(props: SliderProps) {
  const coarse = useCoarsePointer();
  return coarse ? <TouchValue {...props} /> : <RangeSlider {...props} />;
}

function RangeSlider({
  value, min, max, step = 1, onChange, onCommit, format, label, help, marks, disabled, tone, id,
  offLabel,
}: SliderProps) {
  const [draft, setDraft] = useState<number | null>(null);
  const shown = draft ?? value;
  const autoId = useId();
  const inputId = id ?? autoId;
  const pct = max > min ? ((shown - min) / (max - min)) * 100 : 0;
  const text = offLabel && shown <= min ? offLabel : format ? format(shown) : String(shown);
  const commit = () => {
    if (draft !== null) {
      if (draft !== value) onCommit?.(draft);
      setDraft(null);
    }
  };
  return (
    <div className={`slider tone-${tone ?? "accent"} ${disabled ? "disabled" : ""} ${draft !== null ? "dragging" : ""}`}>
      {label && (
        <div className="slider-head">
          <label htmlFor={inputId} className="slider-label">{label}</label>
          <output htmlFor={inputId} className="slider-value num">{text}</output>
        </div>
      )}
      <input
        id={inputId}
        type="range"
        className="slider-input"
        min={min}
        max={max}
        step={step}
        value={shown}
        disabled={disabled}
        aria-valuetext={text}
        onChange={(e) => {
          const v = Number(e.target.value);
          setDraft(v);
          onChange?.(v);
        }}
        onPointerUp={commit}
        onTouchEnd={commit}
        onKeyUp={commit}
        onBlur={commit}
        style={{ ["--pct" as string]: `${pct}%` }}
      />
      {marks && (
        <div className="slider-marks" aria-hidden="true">
          {marks.map((m) => (
            <span key={m.value} style={{ left: `${((m.value - min) / (max - min)) * 100}%` }}>{m.label}</span>
          ))}
        </div>
      )}
      {help && <div className="field-help">{help}</div>}
    </div>
  );
}

/** Aufklappbarer Bereich für seltene Einstellungen ('Erweitert"). */
export function Disclosure({ title, summary, children, defaultOpen = false, id }: {
  title: string; summary?: ReactNode; children: ReactNode; defaultOpen?: boolean; id?: string;
}) {
  return (
    <details className="disclosure" open={defaultOpen} id={id}>
      <summary>
        <span className="disclosure-title">{title}</span>
        {summary && <span className="disclosure-summary">{summary}</span>}
        <Icon name="chevron" size={18} className="disclosure-chevron" />
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}

/** Hinweis im Formular – für riskante Einstellungen und Zusammenhänge. */
export function Callout({ tone = "info", title, children, action }: {
  tone?: "info" | "warn" | "err" | "ok"; title?: string; children: ReactNode; action?: ReactNode;
}) {
  const icon: IconName = tone === "warn" || tone === "err" ? "warning" : tone === "ok" ? "check" : "info";
  return (
    <div className={`callout ${tone}`} role={tone === "err" ? "alert" : undefined}>
      <Icon name={icon} size={18} className="callout-icon" />
      <div className="callout-text">
        {title && <strong>{title}</strong>}
        <div>{children}</div>
        {action && <div className="callout-action">{action}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Status */

export type Tone = "ok" | "warn" | "err" | "info" | "accent";

export function Badge({ tone, children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone ?? ""}`} title={title}>{children}</span>;
}

/** Fehler- und Sonderzustände sind Teil des Designs, nicht ein Sonderfall:
 *  Wenn die Regelung pausiert oder ein Befehl scheitert, gehört das nach oben
 *  und in Klartext – nicht in ein Log. */
export function Banner({ tone = "warn", title, children, action, onDismiss, dismissLabel }: {
  tone?: "ok" | "warn" | "err" | "info"; title: string; children?: ReactNode; action?: ReactNode;
  onDismiss?: () => void;
  dismissLabel?: string;
}) {
  const icon: IconName = { ok: "check", warn: "warning", err: "warning", info: "info" }[tone] as IconName;
  return (
    <div className={`banner ${tone}`} role={tone === "err" ? "alert" : "status"}>
      <span className="banner-icon"><Icon name={icon} size={18} /></span>
      <div className="banner-text">
        <strong>{title}</strong>
        {children && <span>{children}</span>}
      </div>
      {(action || onDismiss) && <div className="banner-actions">
        {action}
        {onDismiss && (
          <button className="banner-close" onClick={onDismiss}
                  title={dismissLabel} aria-label={dismissLabel}><Icon name="close" size={16} /></button>
        )}
      </div>}
    </div>
  );
}

export function StatusDot({ state, title }: { state: "online" | "offline" | "error"; title?: string }) {
  return <span className={`dot ${state}`} title={title} role="img" aria-label={title ?? state} />;
}

export function Bar({ value, max, color, label }: {
  value: number; max: number; color: string; label?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="bar-wrap">
      <div className="bar" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      {label && <span className="bar-label">{label}</span>}
    </div>
  );
}

export function Empty({ icon, title, children, action }: {
  icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="empty">
      {icon && <span className="empty-icon" aria-hidden="true">{icon}</span>}
      <strong>{title}</strong>
      {children && <span className="empty-text">{children}</span>}
      {action}
    </div>
  );
}

/** Große Kennzahl mit Beschriftung (Seitenköpfe von Auto, Warmwasser, Batterie). */
export function Stat({ label, value, sub, tone }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: FigureTone;
}) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value num ${tone ? `t-${tone}` : ""}`}>{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ Zahlen */

export function usePrefersReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}

/** Zahl weich auf einen neuen Wert überführen – kurz, damit sie nie
 *  'hinterherhinkt", wenn man sie ablesen will. */
export function useAnimatedNumber(target: number, durationMs = 600): number {
  const [display, setDisplay] = useState(target);
  const fromRef = useRef(target);
  const startRef = useRef(0);
  const frameRef = useRef(0);

  useEffect(() => {
    const reduced = typeof window !== "undefined"
      && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced || !Number.isFinite(target)) {
      setDisplay(target);
      return;
    }
    fromRef.current = display;
    startRef.current = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - startRef.current) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(fromRef.current + (target - fromRef.current) * eased);
      if (t < 1) frameRef.current = requestAnimationFrame(step);
    };
    frameRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frameRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, durationMs]);

  return display;
}

export function AnimatedValue({ value, format, className, style }: {
  value: number; format: (v: number) => string; className?: string; style?: React.CSSProperties;
}) {
  const animated = useAnimatedNumber(value);
  return <span className={`num ${className ?? ""}`} style={style}>{format(animated)}</span>;
}

/** Farbtöne für Kennzahlen – einer je Energiepfad. */
export type FigureTone = "pv" | "waste" | "import" | "export" | "car" | "battery" | "water";

/** Kennzahl in einem `.figures`-Raster (Statistik). */
export function Figure({ label, value, hint, tone, lead, help, title }: {
  label: string; value: ReactNode; hint?: ReactNode;
  tone?: FigureTone; lead?: boolean; help?: string; title?: string;
}) {
  return (
    <div className={lead ? "figure figure-lead" : "figure"} title={title}>
      <span className="figure-label">
        {label}
        {help && <Help text={help} />}
      </span>
      <span className={tone ? `figure-value num t-${tone}` : "figure-value num"}>{value}</span>
      {hint && <span className="figure-hint">{hint}</span>}
    </div>
  );
}

/** Kleine Kennzahl (Wand-Ansicht, Diagnose). */
export function Metric({ label, value, hint, color, title }: {
  label: string; value: ReactNode; hint?: string; color?: string; title?: string;
}) {
  return (
    <div className="metric" title={title}>
      <span className="metric-value" style={color ? { color } : undefined}>{value}</span>
      <span className="metric-label">{label}</span>
      {hint && <span className="metric-hint">{hint}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ Medien */

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false,
  );
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    setMatches(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** Schmaler Bildschirm (Handy hochkant). */
export function useIsPhone(): boolean {
  return useMediaQuery("(max-width: 640px)");
}

/** Kurzer Aufmerksamkeits-Impuls, wenn sich ein Zustand ändert. */
export function useFlashOnChange(value: unknown): boolean {
  const [flash, setFlash] = useState(false);
  const previous = useRef(value);
  useEffect(() => {
    if (previous.current !== value) {
      previous.current = value;
      setFlash(true);
      const timer = window.setTimeout(() => setFlash(false), 450);
      return () => window.clearTimeout(timer);
    }
  }, [value]);
  return flash;
}

/** Sekundengenaue Restzeit aus einem Snapshot-Wert, der nur alle paar
 *  Sekunden kommt: zählt lokal herunter, statt zu springen. */
export function useCountdown(remainingS: number | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  const base = useRef<{ at: number; remaining: number } | null>(null);
  useEffect(() => {
    base.current = remainingS == null ? null : { at: Date.now(), remaining: remainingS };
  }, [remainingS]);
  useEffect(() => {
    if (remainingS == null) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [remainingS]);
  if (!base.current) return null;
  return Math.max(0, base.current.remaining - (now - base.current.at) / 1000);
}
