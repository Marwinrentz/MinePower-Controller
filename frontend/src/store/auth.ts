/** Anmeldung: Token im Browser halten, gleitend verlängern, und abmelden
 *  ausschließlich dann, wenn der Server das Token wirklich ablehnt.
 *
 *  Der Anmeldeschirm tauchte im Betrieb alle paar Stunden wieder auf, obwohl
 *  das Token zwölf Stunden gültig war — im Protokoll standen sechs Anmeldungen
 *  an einem Tag, im Mittel alle 4,7 Stunden. Zwei Ursachen, beide hier
 *  behoben:
 *
 *  1. **Jeder einzelne Fehlschlag meldete ab.** Ein abgelehnter WebSocket
 *     (Schlusscode 4401) oder eine 401-Antwort führte sofort zum Abmelden —
 *     auch wenn es an einer kurzzeitigen Störung lag (Datenbank gerade neu
 *     gestartet, Anwendung noch im Hochlauf). Jetzt wird erst gegengeprüft:
 *     Nur wenn `/api/auth/me` das Token ebenfalls ablehnt, ist es wirklich
 *     ungültig.
 *  2. **Das Token lief irgendwann einfach ab.** Es wird jetzt regelmäßig
 *     verlängert, solange die Oberfläche offen ist.
 */
import { create } from "zustand";
import { STORAGE } from "../lib/storage";
import type { User } from "../lib/types";

type AuthState = {
  token: string | null;
  user: User | null;
  login: (token: string, user: User) => void;
  logout: () => void;
  /** Token gegen den Server prüfen. Lehnt er es ab, wird abgemeldet.
   *  Liefert true, wenn die Anmeldung weiterhin gilt. */
  verify: () => Promise<boolean>;
  /** Gültigkeit verlängern, solange die Oberfläche benutzt wird. */
  refresh: () => Promise<void>;
};

const TOKEN_KEY = STORAGE.token;
const USER_KEY = STORAGE.user;

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export const useAuth = create<AuthState>((set, get) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: read<User | null>(USER_KEY, null),

  login: (token, user) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    set({ token, user });
  },

  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    set({ token: null, user: null });
  },

  verify: async () => {
    const token = get().token;
    if (!token) return false;
    let resp: Response;
    try {
      resp = await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } });
    } catch {
      // Netzwerk weg, Server neu gestartet, WLAN gewechselt: Darüber lässt
      // sich über die Gültigkeit des Tokens nichts sagen. Angemeldet bleiben.
      return true;
    }
    if (resp.status === 401) {
      get().logout();
      return false;
    }
    return true;
  },

  refresh: async () => {
    const token = get().token;
    if (!token) return;
    try {
      const resp = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) return; // 401 klärt verify(), alles andere ist vorübergehend
      const body = (await resp.json()) as { token: string; user: User };
      if (body?.token) get().login(body.token, body.user ?? get().user!);
    } catch {
      /* offline – beim nächsten Durchgang erneut */
    }
  },
}));

/** Verlängerungstakt. Deutlich kürzer als die Gültigkeit (30 Tage), damit ein
 *  Gerät, das nur alle paar Tage kurz aufgeweckt wird, trotzdem angemeldet
 *  bleibt. */
const REFRESH_EVERY_MS = 6 * 60 * 60 * 1000;
let lastRefresh = 0;

export function keepSessionAlive(): () => void {
  const tick = () => {
    if (!useAuth.getState().token) return;
    if (Date.now() - lastRefresh < REFRESH_EVERY_MS) return;
    lastRefresh = Date.now();
    void useAuth.getState().refresh();
  };
  tick();
  const timer = window.setInterval(tick, 30 * 60 * 1000);
  // Nach dem Aufwachen sofort nachziehen: In einem schlafenden Tab feuert der
  // Timer nicht, und genau dann ist das Token am ehesten alt.
  const onVisible = () => {
    if (document.visibilityState === "visible") tick();
  };
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisible);
  };
}
