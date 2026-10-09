/** Anlagenweite Einstellungen (/api/settings) – ein gemeinsamer Stand für
 *  die Seiten Tarif, Batterie-Einstellungen, Regelung usw.
 *
 *  Gespeichert wird bewusst explizit (Knopf 'Speichern"), nicht bei jeder
 *  Eingabe: Einstellungen wie die Preisgrenze greifen sofort in die Regelung
 *  ein, und ein halb eingetippter Wert ('2" statt '24") darf nicht schon
 *  wirken. Was noch nicht gespeichert ist, zeigt eine Leiste am unteren Rand.
 */
import { create } from "zustand";
import { get as apiGet, put } from "../lib/api";
import { tr } from "../i18n";
import { toast } from "./toast";

export type SettingsMap = Record<string, Record<string, unknown>>;

type SettingsState = {
  values: SettingsMap;
  saved: SettingsMap;
  loaded: boolean;
  saving: boolean;
  load: () => Promise<void>;
  set: (section: string, key: string, value: unknown) => void;
  save: () => Promise<boolean>;
  reset: () => void;
};

const clone = (v: SettingsMap): SettingsMap => JSON.parse(JSON.stringify(v));

export const useSettings = create<SettingsState>((set, get) => ({
  values: {},
  saved: {},
  loaded: false,
  saving: false,

  load: async () => {
    try {
      const r = await apiGet<{ values: SettingsMap }>("/api/settings");
      set({ values: clone(r.values), saved: clone(r.values), loaded: true });
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  },

  set: (section, key, value) =>
    set((s) => ({ values: { ...s.values, [section]: { ...(s.values[section] ?? {}), [key]: value } } })),

  save: async () => {
    if (get().saving) return false;
    set({ saving: true });
    try {
      // Nur geänderte Abschnitte schicken – das Protokoll nennt dann genau
      // die, und ein gleichzeitig anders bearbeiteter Abschnitt bleibt heil.
      const { values, saved } = get();
      const changed: SettingsMap = {};
      for (const [k, v] of Object.entries(values)) {
        if (JSON.stringify(v) !== JSON.stringify(saved[k])) changed[k] = v;
      }
      if (Object.keys(changed).length === 0) return true;
      const r = await put<{ values: SettingsMap }>("/api/settings", { values: changed });
      set({ values: clone(r.values), saved: clone(r.values) });
      toast.ok(tr("common.saved"));
      return true;
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      set({ saving: false });
    }
  },

  reset: () => set((s) => ({ values: clone(s.saved) })),
}));

export function useDirty(): boolean {
  return useSettings((s) => JSON.stringify(s.values) !== JSON.stringify(s.saved));
}

/** Kurzform für einen Abschnitt: Wert lesen mit Vorgabe, Wert setzen. */
export function useSection(section: string) {
  const values = useSettings((s) => s.values[section] ?? {});
  const setVal = useSettings((s) => s.set);
  return {
    v: values,
    num: (key: string, fallback: number) => {
      const n = Number(values[key]);
      return Number.isFinite(n) && values[key] !== null && values[key] !== "" ? n : fallback;
    },
    bool: (key: string, fallback: boolean) =>
      typeof values[key] === "boolean" ? (values[key] as boolean) : fallback,
    str: (key: string, fallback: string) => (values[key] == null ? fallback : String(values[key])),
    set: (key: string, value: unknown) => setVal(section, key, value),
  };
}
