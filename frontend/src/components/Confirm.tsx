/** Kurze Bestätigung für Aktionen mit großer Wirkung (Boost, Volllast,
 *  Batterie sperren …) – nur am Touchscreen.
 *
 *  Am Handy liegen solche Knöpfe oft am Rand des Bildausschnitts, wo ein
 *  Daumen beim Scrollen hängen bleibt. Ein zweiter, bewusster Tipp im Blatt
 *  unten verhindert das. Mit der Maus (Desktop) gibt es dieses Risiko nicht:
 *  dort wirkt der Klick wie bisher sofort.
 *
 *      if (!(await confirmTouch({ title: t('confirm.boostTitle'), … }))) return;
 */
import { tr } from "../i18n";
import { create } from "zustand";
import { haptic, isCoarse } from "../lib/touch";
import { Icon, type IconName } from "./icons";
import { Sheet } from "./Sheet";

export type ConfirmRequest = {
  title: string;
  text?: string;
  confirmLabel: string;
  icon?: IconName;
  tone?: "primary" | "danger";
};

type State = {
  request: (ConfirmRequest & { resolve: (ok: boolean) => void }) | null;
  ask: (r: ConfirmRequest) => Promise<boolean>;
  answer: (ok: boolean) => void;
};

export const useConfirmStore = create<State>((set, get) => ({
  request: null,
  ask: (r) => new Promise<boolean>((resolve) => {
    get().request?.resolve(false);
    set({ request: { ...r, resolve } });
    haptic(6);
  }),
  answer: (ok) => {
    const r = get().request;
    set({ request: null });
    if (ok) haptic([8, 40, 12]);
    r?.resolve(ok);
  },
}));

/** Am Touchscreen bestätigen lassen, mit Maus sofort `true`. */
export function confirmTouch(r: ConfirmRequest): Promise<boolean> {
  if (!isCoarse()) return Promise.resolve(true);
  return useConfirmStore.getState().ask(r);
}

/** Einmal im App-Rahmen einhängen. */
export function ConfirmHost() {
  const request = useConfirmStore((s) => s.request);
  const answer = useConfirmStore((s) => s.answer);
  return (
    <Sheet open={request !== null} onClose={() => answer(false)} label={request?.title} className="confirm-sheet"
           footer={request && (
             <div className="confirm-actions">
               <button type="button" className="mbtn" onClick={() => answer(false)}>{tr("common.cancel")}</button>
               <button type="button" className={`mbtn ${request.tone === "danger" ? "danger" : "primary"}`}
                       onClick={() => answer(true)} data-confirm="yes">
                 {request.icon && <Icon name={request.icon} size={20} />}
                 {request.confirmLabel}
               </button>
             </div>
           )}>
      {request && (
        <div className="confirm-body">
          {request.icon && <span className={`confirm-icon ${request.tone ?? "primary"}`}><Icon name={request.icon} size={28} /></span>}
          <h2 className="confirm-title">{request.title}</h2>
          {request.text && <p className="confirm-text">{request.text}</p>}
        </div>
      )}
    </Sheet>
  );
}
