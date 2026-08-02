import { create } from "zustand";

export type ThemeMode = "light" | "dark" | "system";

function apply(mode: ThemeMode) {
  const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

type ThemeState = { mode: ThemeMode; setMode: (m: ThemeMode) => void };

export const useTheme = create<ThemeState>((set) => {
  const initial = (localStorage.getItem("sc_theme") as ThemeMode) || "system";
  apply(initial);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const current = (localStorage.getItem("sc_theme") as ThemeMode) || "system";
    if (current === "system") apply("system");
  });
  return {
    mode: initial,
    setMode: (mode) => {
      localStorage.setItem("sc_theme", mode);
      apply(mode);
      set({ mode });
    },
  };
});
