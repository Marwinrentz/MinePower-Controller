/** UI-Bausteine des MinePower-Design-Systems.
 *
 *  Alles, was auf mehr als einer Seite vorkommt, lebt hier – damit Farben,
 *  Abstände, Zustände und Bewegung überall gleich funktionieren und kein
 *  Stilbruch zwischen Dashboard, Assistent und Einstellungen entsteht.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";

/* ------------------------------------------------------------------ Flächen */

export function Card({ children, className = "", style, onClick }: {
  children: ReactNode; className?: string; style?: React.CSSProperties; onClick?: () => void;
}) {
  return <div className={`card ${className}`} style={style} onClick={onClick}>{children}</div>;
}

export function Panel({ children, className = "", style }: {
  children: ReactNode; className?: string; style?: React.CSSProperties;
}) {
  return <div className={`panel ${className}`} style={style}>{children}</div>;
}

export function CardHead({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="card-head">
      {typeof title === "string" ? <h2>{title}</h2> : title}
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ Aktionen */

export function Button({
  children, onClick, variant = "", disabled = false, pressed = false, small = false,
  block = false, icon = false, type = "button", title, ariaLabel,
}: {
  children: ReactNode; onClick?: () => void;
  variant?: "" | "primary" | "danger" | "ghost";
  disabled?: boolean; pressed?: boolean; small?: boolean; block?: boolean; icon?: boolean;
  type?: "button" | "submit"; title?: string; ariaLabel?: string;
}) {
  const classes = ["btn", variant, pressed ? "pressed" : "", small ? "small" : "",
    block ? "block" : "", icon ? "icon" : ""].filter(Boolean).join(" ");
  return (
    <button
      type={type}
      title={title}
      aria-label={ariaLabel ?? title}
      aria-pressed={pressed || undefined}
      className={classes}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

export function Toggle({ on, onChange, label, disabled = false }: {
  on: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean;
}) {
  return (
    <div className="row tight">
      <button
        type="button"
        className={`switch ${on ? "on" : ""}`}
        role="switch"
        aria-checked={on}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!on)}
      />
      {label && <span style={{ fontSize: "var(--text-sm)" }}>{label}</span>}
    </div>
  );
}

export function Segment<T extends string>({ options, value, onChange, ariaLabel }: {
  options: { value: T; label: string; title?: string }[];
  value: T; onChange: (v: T) => void; ariaLabel?: string;
}) {
  return (
    <div className="segment" role="tablist" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          title={o.title}
          aria-selected={o.value === value}
          className={o.value === value ? "active" : ""}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ Formular */

export function Help({ text }: { text: string }) {
  return (
    <span className="tip" tabIndex={0}>
      <span className="tip-icon" aria-hidden="true">i</span>
      <span className="tip-text" role="tooltip">{text}</span>
    </span>
  );
}

export function Field({ label, help, children, hint }: {
  label: string; help?: string | null; children: ReactNode; hint?: string;
}) {
  return (
    <div className="field">
      <label>
        {label}
        {help ? <Help text={help} /> : null}
      </label>
      {children}
      {hint && <small>{hint}</small>}
    </div>
  );
}

/* ------------------------------------------------------------------ Status */

export type Tone = "ok" | "warn" | "err" | "info" | "accent";

export function Badge({ tone, children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge ${tone ?? ""}`}>{children}</span>;
}

/** Fehler- und Sonderzustände sind Teil des Designs, nicht ein Sonderfall:
 *  Wenn die Regelung pausiert oder ein Befehl scheitert, gehört das nach oben
 *  und in Klartext – nicht in ein Log. */
export function Banner({ tone = "warn", title, children, action }: {
  tone?: "ok" | "warn" | "err" | "info"; title: string; children?: ReactNode; action?: ReactNode;
}) {
  const icon = { ok: "✓", warn: "⚠", err: "✕", info: "ℹ" }[tone];
  return (
    <div className={`banner ${tone}`} role={tone === "err" ? "alert" : "status"}>
      <span className="banner-icon" aria-hidden="true">{icon}</span>
      <div className="banner-text">
        <strong>{title}</strong>
        {children && <span>{children}</span>}
      </div>
      {action && <><div className="spacer" />{action}</>}
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

export function Empty({ icon = "◌", title, children }: {
  icon?: string; title: string; children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon" aria-hidden="true">{icon}</span>
      <strong style={{ color: "var(--text-strong)" }}>{title}</strong>
      {children && <span style={{ maxWidth: 440 }}>{children}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ Zahlen */

/** Zahl weich auf einen neuen Wert überführen.
 *
 *  Leistungswerte springen im 3-Sekunden-Takt. Ein harter Sprung liest sich
 *  wie ein Fehler; eine kurze Interpolation lässt erkennen, in welche Richtung
 *  sich die Anlage bewegt. Bewusst kurz gehalten – die Zahl soll nie
 *  „hinterherhinken“, wenn man sie ablesen will.
 */
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
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      setDisplay(fromRef.current + (target - fromRef.current) * eased);
      if (t < 1) frameRef.current = requestAnimationFrame(step);
    };
    frameRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frameRef.current);
    // display bewusst nicht in den Abhängigkeiten: sonst startet jede
    // Zwischenstufe eine neue Animation.
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

/** Einzelne Kennzahl in der Kopfleiste des Dashboards. */
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

/** Reagiert auf eine CSS-Media-Query.
 *
 *  Für alles, was sich per CSS lösen lässt, bleibt CSS zuständig. Dieser Hook
 *  ist für die Fälle, in denen sich die *Struktur* unterscheidet – etwa wenn
 *  das Handy statt des Fluss-Diagramms die Energieleiste bekommt. Ein 660 px
 *  breites SVG auf ein Handy zu quetschen macht es unlesbar, egal wie gut es
 *  skaliert; dort ist eine andere Darstellung die richtige Antwort. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" && window.matchMedia
      ? window.matchMedia(query).matches
      : false,
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

/** „Ist das ein Handy?" – Breite UND Zeigergenauigkeit.
 *
 *  Nur die Breite abzufragen wäre falsch: Ein schmales Browserfenster am
 *  Desktop ist kein Handy, und ein Tablet im Querformat ist breit, wird aber
 *  mit dem Finger bedient. `pointer: coarse` unterscheidet Finger von Maus. */
export function useIsPhone(): boolean {
  return useMediaQuery("(max-width: 760px), (pointer: coarse) and (max-width: 900px)");
}

/** Kurzer Aufmerksamkeits-Impuls, wenn sich ein Zustand ändert
 *  (Ladung startet/stoppt, Phasenumschaltung, Gerät geht offline). */
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
