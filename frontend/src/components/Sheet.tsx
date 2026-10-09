/** Blatt von unten (Bottom-Sheet) – das Detail-Fenster der Handy-App.
 *
 *  • Federt herein (Spring-Kurve), gleitet beim Schließen hinaus.
 *  • Zum Schließen: Hintergrund antippen, Escape, oder am Griff bzw. Titel
 *    nach unten ziehen. Ziehen geht nur dort – der Inhalt darunter scrollt
 *    ganz normal und kann das Blatt nicht versehentlich schließen.
 *  • Aktionen stehen im Fuß, also im Daumenbereich, über dem
 *    Home-Indikator (Safe-Area).
 *  • Solange es offen ist, scrollt die Seite dahinter nicht mit.
 *  • Querformat: mittig und schmaler, statt über die ganze Breite.
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { haptic } from "../lib/touch";
import { tr } from "../i18n";
import { Icon } from "./icons";

const CLOSE_MS = 240;
/** So weit (px) oder so schnell (px/ms) nach unten gezogen, schließt es. */
const DISMISS_PX = 96;
const DISMISS_SPEED = 0.6;

let openSheets = 0;

function lockScroll(lock: boolean) {
  openSheets = Math.max(0, openSheets + (lock ? 1 : -1));
  document.documentElement.classList.toggle("sheet-open", openSheets > 0);
}

export function Sheet({ open, onClose, title, children, footer, className = "", label }: {
  open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; footer?: ReactNode;
  className?: string; label?: string;
}) {
  const [mounted, setMounted] = useState(open);
  const [closing, setClosing] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y: number; t: number; dy: number } | null>(null);
  const titleId = useId();
  const returnFocus = useRef<Element | null>(null);

  useEffect(() => {
    if (open) {
      setMounted(true);
      setClosing(false);
      return;
    }
    if (!mounted) return;
    setClosing(true);
    const timer = window.setTimeout(() => { setMounted(false); setClosing(false); }, CLOSE_MS);
    return () => window.clearTimeout(timer);
  }, [open, mounted]);

  useEffect(() => {
    if (!mounted) return;
    lockScroll(true);
    returnFocus.current = document.activeElement;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    // Fokus ins Blatt (Screenreader, Tastatur), aber ohne Tastatur am Handy
    // aufzuklappen: das Blatt selbst bekommt den Fokus, nicht das erste Feld.
    window.setTimeout(() => panel.current?.focus({ preventScroll: true }), 30);
    return () => {
      lockScroll(false);
      window.removeEventListener("keydown", onKey);
      (returnFocus.current as HTMLElement | null)?.focus?.({ preventScroll: true });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mounted]);

  if (!mounted) return null;

  const setOffset = (dy: number, animate: boolean) => {
    const el = panel.current;
    if (!el) return;
    el.style.transition = animate ? "transform 260ms cubic-bezier(0.2, 0.9, 0.3, 1)" : "none";
    el.style.transform = dy ? `translate3d(0, ${dy}px, 0)` : "";
  };

  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    drag.current = { y: e.clientY, t: performance.now(), dy: 0 };
    try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); } catch { /* Zeiger schon weg */ }
  };
  const onMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const dy = Math.max(0, e.clientY - drag.current.y);
    drag.current.dy = dy;
    setOffset(dy, false);
  };
  const onUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    const speed = d.dy / Math.max(1, performance.now() - d.t);
    if (d.dy > DISMISS_PX || (d.dy > 24 && speed > DISMISS_SPEED)) {
      haptic(8);
      onClose();
    } else {
      setOffset(0, true);
    }
  };

  return createPortal(
    <div className={`msheet-root ${closing ? "closing" : ""}`}>
      <div className="msheet-backdrop" onClick={onClose} aria-hidden="true" />
      <div ref={panel} className={`msheet ${className}`} role="dialog" aria-modal="true" tabIndex={-1}
           aria-labelledby={title ? titleId : undefined} aria-label={title ? undefined : label}>
        <div className="msheet-head" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
             onPointerCancel={onUp}>
          <div className="msheet-grip" aria-hidden="true" />
          {title && (
            <div className="msheet-title-row">
              <h2 id={titleId} className="msheet-title">{title}</h2>
              <button type="button" className="msheet-close" onClick={onClose} aria-label={tr("common.close")}
                      onPointerDown={(e) => e.stopPropagation()}>
                <Icon name="close" size={20} />
              </button>
            </div>
          )}
        </div>
        <div className="msheet-body">{children}</div>
        {footer && <div className="msheet-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
