/** Leiste am unteren Rand, solange Einstellungen ungespeichert sind.
 *  Steht über der Tab-Leiste und lässt sich nicht übersehen – vorher lag der
 *  einzige Speichern-Knopf oben rechts, weit weg vom geänderten Feld. */
import { useI18n } from "../i18n";
import { useDirty, useSettings } from "../store/settings";
import { ActionButton, Button } from "./ui";

export function SaveBar() {
  const { t } = useI18n();
  const dirty = useDirty();
  const save = useSettings((s) => s.save);
  const reset = useSettings((s) => s.reset);
  if (!dirty) return null;
  return (
    <div className="savebar" role="region" aria-label={t("set.unsaved")}>
      <span className="savebar-text">{t("set.unsaved")}</span>
      <Button variant="ghost" onClick={reset}>{t("set.discard")}</Button>
      <ActionButton variant="primary" onClick={save}>{t("set.save")}</ActionButton>
    </div>
  );
}
