/** Neue Version auf dem Server? Dann soll die Oberfläche das sagen, statt
 *  still die alte weiterzuzeigen – nach dem Update auf 2.14 fehlten so
 *  scheinbar neue Knöpfe, weil der Browser noch die alte Seite hatte. */
import { useEffect, useState } from "react";

const CHECK_EVERY_MS = 5 * 60 * 1000;

export const APP_VERSION: string = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "dev";

export function useServerVersion(): { server: string | null; outdated: boolean } {
  const [server, setServer] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const check = () => {
      fetch("/api/system/health", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => alive && d?.version && setServer(String(d.version)))
        .catch(() => {});
    };
    check();
    const timer = window.setInterval(check, CHECK_EVERY_MS);
    const onVisible = () => document.visibilityState === "visible" && check();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);
  const outdated = server !== null && APP_VERSION !== "dev" && server !== APP_VERSION;
  return { server, outdated };
}
