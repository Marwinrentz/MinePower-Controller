/** Live-Snapshot via WebSocket mit Auto-Reconnect. */
import { create } from "zustand";
import type { Snapshot } from "../lib/types";
import { useAuth } from "./auth";

type LiveState = {
  snapshot: Snapshot | null;
  connected: boolean;
  connect: () => void;
  disconnect: () => void;
};

let ws: WebSocket | null = null;
let retryTimer: number | null = null;
let retryDelay = 1000;
let wanted = false;

export const useLive = create<LiveState>((set) => ({
  snapshot: null,
  connected: false,
  connect: () => {
    wanted = true;
    openSocket(set);
  },
  disconnect: () => {
    wanted = false;
    if (retryTimer) window.clearTimeout(retryTimer);
    ws?.close();
    ws = null;
    set({ connected: false });
  },
}));

function openSocket(set: (s: Partial<LiveState>) => void) {
  const token = useAuth.getState().token;
  if (!token || !wanted || (ws && ws.readyState <= WebSocket.OPEN)) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${encodeURIComponent(token)}`);
  ws.onopen = () => {
    retryDelay = 1000;
    set({ connected: true });
  };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "snapshot") set({ snapshot: msg.data });
    } catch {
      /* ignorieren */
    }
  };
  ws.onclose = () => {
    set({ connected: false });
    ws = null;
    if (wanted) {
      retryTimer = window.setTimeout(() => openSocket(set), retryDelay);
      retryDelay = Math.min(retryDelay * 2, 15000);
    }
  };
}
