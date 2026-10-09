import { create } from "zustand";
import { STORAGE } from "../lib/storage";

export type ThemeMode = "light" | "dark" | "system";

function apply(mode: ThemeMode) {
  const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  // Browser-/Statusleiste mitfärben (Safari, Chrome Android). Der
  // Statusleisten-Stil der iOS-App gilt erst beim nächsten Start.
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0d1015" : "#f3f5f8");
  document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]')
    ?.setAttribute("content", dark ? "black-translucent" : "default");
}

type ThemeState = { mode: ThemeMode; setMode: (m: ThemeMode) => void };

// Speicher kann in manchen WebViews/privaten Fenstern fehlen – dann gilt 'system".
function stored(): ThemeMode {
  try {
    return (localStorage.getItem(STORAGE.theme) as ThemeMode) || "system";
  } catch {
    return "system";
  }
}

export const useTheme = create<ThemeState>((set) => {
  const initial = stored();
  apply(initial);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (stored() === "system") apply("system");
  });
  return {
    mode: initial,
    setMode: (mode) => {
      try { localStorage.setItem(STORAGE.theme, mode); } catch { /* nur für diese Sitzung */ }
      apply(mode);
      set({ mode });
    },
  };
});
