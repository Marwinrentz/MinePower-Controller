import { createContext, useCallback, useContext, useState } from "react";
import { de, type Dict } from "./de";
import { en } from "./en";

const dicts: Record<string, Dict> = { de, en };

type I18nCtx = { lang: string; setLang: (l: string) => void; t: (path: string) => string; dict: Dict };

const Ctx = createContext<I18nCtx>({ lang: "de", setLang: () => {}, t: (p) => p, dict: de });

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState(() => localStorage.getItem("sc_lang") || (navigator.language.startsWith("de") ? "de" : "en"));
  const setLang = useCallback((l: string) => {
    localStorage.setItem("sc_lang", l);
    setLangState(l);
  }, []);
  const dict = dicts[lang] ?? de;
  const t = useCallback(
    (path: string): string => {
      let node: unknown = dict;
      for (const key of path.split(".")) {
        if (node && typeof node === "object" && key in (node as Record<string, unknown>)) {
          node = (node as Record<string, unknown>)[key];
        } else return path;
      }
      return typeof node === "string" ? node : path;
    },
    [dict],
  );
  return <Ctx.Provider value={{ lang, setLang, t, dict }}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);
