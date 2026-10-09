/** Lokaler Entwurf für Geräte-Einstellungen: erst sammeln, dann speichern.
 *  Wird zurückgesetzt, sobald sich die gespeicherten Werte ändern (z. B. nach
 *  dem Speichern oder wenn jemand anderes etwas geändert hat). */
import { useEffect, useMemo, useState } from "react";

export function useDraft(saved: Record<string, unknown> | undefined) {
  const key = JSON.stringify(saved ?? {});
  const [draft, setDraft] = useState<Record<string, unknown>>(saved ?? {});
  useEffect(() => setDraft(saved ?? {}), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = useMemo(() => JSON.stringify(draft) !== key, [draft, key]);
  return {
    draft,
    dirty,
    set: (k: string, v: unknown) => setDraft((d) => ({ ...d, [k]: v })),
    num: (k: string, fallback: number) => {
      const n = Number(draft[k]);
      return draft[k] !== undefined && draft[k] !== null && draft[k] !== "" && Number.isFinite(n) ? n : fallback;
    },
    str: (k: string, fallback: string) => (draft[k] == null ? fallback : String(draft[k])),
    bool: (k: string, fallback: boolean) => (typeof draft[k] === "boolean" ? (draft[k] as boolean) : fallback),
    reset: () => setDraft(saved ?? {}),
  };
}
