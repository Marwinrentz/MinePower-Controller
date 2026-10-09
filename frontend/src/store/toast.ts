/** Kurze Rückmeldungen ('Gespeichert", 'Befehl abgelehnt: …").
 *
 *  Ein Knopf, der nach dem Drücken nichts sagt, wird ein zweites Mal
 *  gedrückt – im Diagnosebericht viermal in 25 Sekunden. Jede Aktion meldet
 *  deshalb sichtbar, ob sie angekommen ist.
 *
 *  Eine Meldung kann eine Aktion tragen ('Rückgängig"): Am Handy wird jeder
 *  geänderte Wert so bestätigt, dass ein Versehen mit einem Tipp zurückgeht.
 */
import { tr } from "../i18n";
import { create } from "zustand";

export type ToastTone = "ok" | "err" | "info";
export type ToastAction = { label: string; run: () => void };
export type Toast = { id: number; text: string; tone: ToastTone; action?: ToastAction };

type ToastState = {
  toasts: Toast[];
  show: (text: string, tone?: ToastTone, action?: ToastAction) => void;
  dismiss: (id: number) => void;
};

let next = 1;

/** Mit Aktion länger stehen lassen – man muss den Knopf noch erreichen. */
const DURATION = { ok: 2600, info: 2600, err: 7000, action: 6000 };

export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  show: (text, tone = "ok", action) => {
    const id = next++;
    // Dieselbe Meldung nicht stapeln (z. B. derselbe Fehler zweimal).
    const rest = get().toasts.filter((t) => t.text !== text);
    set({ toasts: [...rest.slice(-2), { id, text, tone, action }] });
    window.setTimeout(() => get().dismiss(id), action ? DURATION.action : DURATION[tone]);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));

export const toast = {
  ok: (text: string) => useToasts.getState().show(text, "ok"),
  err: (text: string) => useToasts.getState().show(text, "err"),
  info: (text: string) => useToasts.getState().show(text, "info"),
  /** Meldung mit 'Rückgängig". */
  undo: (text: string, run: () => void, label = tr("common.undo")) =>
    useToasts.getState().show(text, "ok", { label, run }),
};
