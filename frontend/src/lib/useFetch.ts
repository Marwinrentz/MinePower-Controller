/** Daten laden mit sichtbarem Zustand: lädt, geladen, fehlgeschlagen.
 *
 *  Vorher schluckten viele Ansichten einen fehlgeschlagenen Abruf
 *  (`.catch(() => {})`) und zeigten dann für immer den Ladezustand. Hier
 *  bleibt bei einem Fehler der letzte gute Stand stehen, der Fehler ist
 *  abfragbar, und `reload()` versucht es erneut. */
import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";

export type Fetched<T> = {
  data: T | null;
  /** Meldung des letzten Fehlschlags; null, sobald wieder ein Abruf gelingt. */
  error: string | null;
  /** Erster Abruf läuft noch (noch nie Daten und noch kein Fehler). */
  loading: boolean;
  reload: () => void;
};

export function useFetch<T>(url: string | null, options: { intervalMs?: number } = {}): Fetched<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const loaded = useRef(false);
  const { intervalMs } = options;

  useEffect(() => {
    if (url === null) return;
    let alive = true;
    const load = () => {
      get<T>(url).then((value) => {
        if (!alive) return;
        loaded.current = true;
        setData(value);
        setError(null);
      }).catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      });
    };
    load();
    if (!intervalMs) return () => { alive = false; };
    const timer = window.setInterval(load, intervalMs);
    return () => { alive = false; window.clearInterval(timer); };
  }, [url, intervalMs, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading: data === null && error === null, reload };
}
