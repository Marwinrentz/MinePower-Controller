/** Touch-Hilfen: Geräteart, Haptik, Gesten-Schwelle.
 *
 *  Am Handy gilt: Scrollen hat immer Vorrang. Eine Bewegung wird erst dann
 *  als Bedienung gewertet, wenn sie eindeutig in die Richtung des
 *  Bedienelements geht und eine Mindeststrecke zurücklegt – sonst gehört
 *  sie dem Scrollen.
 */
import { useEffect, useState } from "react";

/** Eigene kleine Variante von useMediaQuery – ui.tsx importiert dieses
 *  Modul, ein Rückimport wäre zirkulär. */
function useMatch(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false,
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

/** Finger statt Maus (Handy, Tablet). Desktop mit Maus bleibt unberührt. */
export const COARSE = "(pointer: coarse)";

export function isCoarse(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.(COARSE).matches;
}

export function useCoarsePointer(): boolean {
  return useMatch(COARSE);
}

/** Handy hochkant oder quer (nicht Tablet, nicht Desktop). */
export const HANDHELD = "(max-width: 640px), (max-width: 1023px) and (max-height: 540px) and (orientation: landscape)";

export function useHandheld(): boolean {
  return useMatch(HANDHELD);
}

/** Kurzer Vibrationsimpuls, wo verfügbar (Android). iOS ignoriert ihn. */
export function haptic(pattern: number | number[] = 10): void {
  try {
    navigator.vibrate?.(pattern);
  } catch {
    /* nicht unterstützt */
  }
}

/** Ab dieser Strecke (px) ist eine Bewegung eine Geste. */
export const GESTURE_SLOP = 10;

export type GestureAxis = "x" | "y" | null;

/** Welche Richtung hat die Bewegung? `null`, solange sie zu kurz ist.
 *  Waagerecht zählt nur, wenn sie deutlich überwiegt (Faktor 1,3) –
 *  ein schräger Wisch beim Scrollen bleibt Scrollen. */
export function gestureAxis(dx: number, dy: number, slop = GESTURE_SLOP): GestureAxis {
  const ax = Math.abs(dx), ay = Math.abs(dy);
  if (ax < slop && ay < slop) return null;
  return ax > ay * 1.3 ? "x" : "y";
}
