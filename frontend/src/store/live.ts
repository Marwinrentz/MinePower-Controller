/** Live-Snapshot via WebSocket mit Auto-Reconnect.
 *
 *  Der Regler schickt im Sekundentakt Momentaufnahmen. Eine offene Verbindung
 *  ist aber keine Zusage, dass auch etwas ankommt: Ein schlafendes Handy, ein
 *  Router-Neustart oder ein abgelaufenes Token hinterlassen einen Socket, der
 *  'offen' aussieht und nie wieder liefert. Genau daraus entstand der Eindruck
 *  eines eingefrorenen Servers, der sich nur durch Neuladen der Seite beheben
 *  ließ. Deshalb hier drei Sicherungen statt einer:
 *
 *    1. Schlusscode 4401 → abgelaufenes Token, direkt zur Anmeldung.
 *    2. Rückkehr aus dem Hintergrund → sofort neu verbinden, nicht erst,
 *       wenn ein gedrosselter Timer irgendwann feuert.
 *    3. Totmann-Schaltung → kommt trotz offener Verbindung nichts mehr,
 *       wird sie verworfen und neu aufgebaut.
 */
import { create } from "zustand";
import type { Snapshot } from "../lib/types";
import { useAuth } from "./auth";

type LiveState = {
  snapshot: Snapshot | null;
  connected: boolean;
  /** Verbindung steht, aber es kommen keine Daten mehr (siehe STALE_AFTER_MS). */
  stale: boolean;
  connect: () => void;
  disconnect: () => void;
};

/** Ohne Nachricht binnen dieser Zeit gilt die Verbindung als tot. Der Regler
 *  sendet je Takt (Standard 3 s, hier bis 15 s konfigurierbar); 45 s lassen
 *  auch einem langsamen Takt Luft, ohne den Nutzer minutenlang im Dunkeln zu
 *  lassen. */
const STALE_AFTER_MS = 45_000;
const WATCHDOG_INTERVAL_MS = 5_000;

let ws: WebSocket | null = null;
let retryTimer: number | null = null;
let watchdog: number | null = null;
let retryDelay = 1000;
let wanted = false;
let lastMessageAt = 0;

export const useLive = create<LiveState>((set) => {
  /** Verbindung hart verwerfen und sofort neu aufbauen. */
  const restart = () => {
    if (ws) {
      ws.onclose = null; // kein doppelter Reconnect über den Handler
      ws.close();
      ws = null;
    }
    set({ connected: false });
    openSocket(set);
  };

  const startWatchdog = () => {
    if (watchdog !== null) return;
    watchdog = window.setInterval(() => {
      if (!wanted || !ws || ws.readyState !== WebSocket.OPEN) return;
      const silent = Date.now() - lastMessageAt;
      if (silent > STALE_AFTER_MS) {
        // Der Socket meldet 'offen', liefert aber nichts mehr. Das passiert,
        // wenn die Gegenstelle verschwindet, ohne den Socket zu schließen –
        // TCP merkt das von sich aus erst nach Minuten bis Stunden.
        set({ stale: true });
        restart();
      }
    }, WATCHDOG_INTERVAL_MS);
  };

  // Zurück aus dem Hintergrund: Mobile Browser drosseln Timer in inaktiven
  // Tabs bis auf einmal pro Minute und frieren sie im Standby ganz ein. Der
  // Backoff-Timer feuert dann evtl. minutenlang nicht – hier stattdessen
  // sofort nachsehen, sobald der Nutzer wieder hinschaut.
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible" || !wanted) return;
      if (!ws || ws.readyState > WebSocket.OPEN) {
        retryDelay = 1000;
        openSocket(set);
      } else if (Date.now() - lastMessageAt > STALE_AFTER_MS) {
        restart();
      }
    });
    window.addEventListener("online", () => {
      if (!wanted) return;
      retryDelay = 1000;
      restart();
    });
  }

  return {
    snapshot: null,
    connected: false,
    stale: false,
    connect: () => {
      wanted = true;
      lastMessageAt = Date.now();
      startWatchdog();
      openSocket(set);
    },
    disconnect: () => {
      wanted = false;
      if (retryTimer) window.clearTimeout(retryTimer);
      if (watchdog !== null) {
        window.clearInterval(watchdog);
        watchdog = null;
      }
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      ws = null;
      set({ connected: false, stale: false });
    },
  };
});

function openSocket(set: (s: Partial<LiveState>) => void) {
  const token = useAuth.getState().token;
  if (!token || !wanted || (ws && ws.readyState <= WebSocket.OPEN)) return;
  if (retryTimer) {
    window.clearTimeout(retryTimer);
    retryTimer = null;
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${encodeURIComponent(token)}`);
  ws.onopen = () => {
    retryDelay = 1000;
    lastMessageAt = Date.now();
    set({ connected: true, stale: false });
  };
  ws.onmessage = (ev) => {
    lastMessageAt = Date.now();
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "snapshot") set({ snapshot: msg.data, stale: false });
    } catch {
      /* ignorieren */
    }
  };
  ws.onclose = (ev) => {
    set({ connected: false });
    ws = null;
    // 4401 = das Backend hat das Token abgelehnt. Nicht sofort abmelden:
    // Der Code sagt nur, dass DIESE Prüfung fehlschlug — beim Hochlauf oder
    // nach einem Datenbank-Neustart kann das vorübergehend sein. Erst
    // gegenprüfen; gilt die Anmeldung noch, wird normal weiterverbunden.
    if (ev.code === 4401) {
      void useAuth.getState().verify().then((stillValid) => {
        if (stillValid && wanted) {
          retryTimer = window.setTimeout(() => openSocket(set), retryDelay);
          retryDelay = Math.min(retryDelay * 2, 15000);
        }
      });
      return;
    }
    if (wanted) {
      retryTimer = window.setTimeout(() => openSocket(set), retryDelay);
      retryDelay = Math.min(retryDelay * 2, 15000);
    }
  };
}
