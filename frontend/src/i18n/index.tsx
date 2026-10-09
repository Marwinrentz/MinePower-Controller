import { createContext, useCallback, useContext, useState } from "react";
import { setFormatLocale } from "../lib/format";
import { STORAGE } from "../lib/storage";
import { de, type Dict } from "./de";
import { en } from "./en";

/** Englisch darf unvollständig sein: Fehlt ein Text, wird der deutsche
 *  angezeigt statt des rohen Schlüssels. Deutsch ist die Leitsprache. */
const dicts: Record<string, unknown> = { de, en };

export type Vars = Record<string, string | number>;
export type T = (path: string, vars?: Vars) => string;

type I18nCtx = { lang: string; setLang: (l: string) => void; t: T; dict: Dict };

const Ctx = createContext<I18nCtx>({ lang: "de", setLang: () => {}, t: (p) => p, dict: de });

function lookup(dict: unknown, path: string): string | null {
  let node: unknown = dict;
  for (const key of path.split(".")) {
    if (node && typeof node === "object" && key in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[key];
    } else return null;
  }
  return typeof node === "string" ? node : null;
}

/** Platzhalter {name} ersetzen; fehlende bleiben sichtbar stehen. */
function fill(text: string, vars?: Vars): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}

/** Aktive Sprache für Code außerhalb von Komponenten (Stores, Hilfsfunktionen). */
let active: unknown = de;

export const tr: T = (path, vars) => fill(lookup(active, path) ?? lookup(de, path) ?? path, vars);

function readLang(): string {
  try {
    const stored = localStorage.getItem(STORAGE.lang);
    if (stored) return stored;
  } catch {
    /* Speicher gesperrt (privater Modus) – Browsersprache nehmen */
  }
  return navigator.language.startsWith("de") ? "de" : "en";
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState(readLang);
  const setLang = useCallback((l: string) => {
    try {
      localStorage.setItem(STORAGE.lang, l);
    } catch {
      /* egal – gilt dann nur bis zum Neuladen */
    }
    setLangState(l);
  }, []);
  const dict = dicts[lang] ?? de;
  active = dict;
  setFormatLocale(lang);
  const t = useCallback(
    (path: string, vars?: Vars): string => fill(lookup(dict, path) ?? lookup(de, path) ?? path, vars),
    [dict],
  );
  return <Ctx.Provider value={{ lang, setLang, t, dict: de }}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);
