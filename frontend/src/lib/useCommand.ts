/** Gerätebefehle mit sofortiger Rückmeldung.
 *
 *  1. **Optimistisch:** Der gewünschte Zustand erscheint sofort, nicht erst
 *     mit dem nächsten Messwert (bei einem Fahrzeug über Funk mehrere
 *     Sekunden später).
 *  2. **Bestätigt:** Sobald der Live-Snapshot den Zustand meldet, fällt der
 *     Wunsch weg – ab dann zeigt die Oberfläche wieder die Wirklichkeit.
 *  3. **Doppelklick-Schutz:** Ein identischer Befehl, der noch unterwegs ist,
 *     wird nicht ein zweites Mal geschickt.
 *  4. **Ehrlich bei Fehlern:** Lehnt das Backend ab, springt die Anzeige
 *     zurück und der Grund erscheint als Meldung.
 */
import { useEffect, useRef, useState } from "react";
import { post } from "./api";
import { toast } from "../store/toast";

/** Liefert ein Gerät die Bestätigung nie (Funk weg), darf die Anzeige nicht
 *  dauerhaft etwas Falsches behaupten. */
const CONFIRM_TIMEOUT_MS = 25_000;

export type Command = {
  /** Anzuzeigender Wert: der gewünschte, solange er aussteht, sonst der echte. */
  shown: <T>(key: string, real: T) => T;
  /** Wartet dieser Wert noch auf Bestätigung? */
  pending: (key: string) => boolean;
  /** Läuft gerade irgendein Befehl (HTTP unterwegs)? */
  busy: boolean;
  send: (action: string, value?: unknown, optimistic?: Record<string, unknown>) => Promise<boolean>;
  error: string | null;
};

export function useDeviceCommand(deviceId: number | undefined, confirmed: Record<string, unknown>): Command {
  const [want, setWant] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef(new Set<string>());
  const timers = useRef<Record<string, number>>({});

  // Bestätigt der Snapshot den Wunsch, ist er erledigt.
  useEffect(() => {
    const done = Object.keys(want).filter((k) => Object.is(want[k], confirmed[k]));
    if (done.length === 0) return;
    setWant((w) => {
      const n = { ...w };
      for (const k of done) {
        delete n[k];
        window.clearTimeout(timers.current[k]);
      }
      return n;
    });
  }, [confirmed, want]);

  useEffect(() => () => Object.values(timers.current).forEach(window.clearTimeout), []);

  const send = async (action: string, value?: unknown, optimistic: Record<string, unknown> = {}) => {
    if (deviceId === undefined) return false;
    const sig = `${action}:${JSON.stringify(value ?? null)}`;
    if (inflight.current.has(sig)) return false;
    inflight.current.add(sig);
    setError(null);
    setBusy(true);
    setWant((w) => ({ ...w, ...optimistic }));
    for (const k of Object.keys(optimistic)) {
      window.clearTimeout(timers.current[k]);
      timers.current[k] = window.setTimeout(() => {
        setWant((w) => {
          const n = { ...w };
          delete n[k];
          return n;
        });
      }, CONFIRM_TIMEOUT_MS);
    }
    try {
      await post(`/api/devices/${deviceId}/action`, { action, value });
      return true;
    } catch (e) {
      setWant((w) => {
        const n = { ...w };
        for (const k of Object.keys(optimistic)) delete n[k];
        return n;
      });
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      toast.err(msg);
      return false;
    } finally {
      inflight.current.delete(sig);
      setBusy(inflight.current.size > 0);
    }
  };

  return {
    shown: <T,>(key: string, real: T) => (key in want ? (want[key] as T) : real),
    pending: (key: string) => key in want,
    busy,
    send,
    error,
  };
}
