/** Hinweisfenster nach einem Update: Änderungen seit der zuletzt gesehenen
 *  Version (gespeichert je Konto im Backend). Schließen markiert gesehen. */
import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { loadChangelog, markSeen, type ChangelogEntry } from "../lib/changelog";
import { useAuth } from "../store/auth";
import { ChangelogList } from "./ChangelogList";
import { Sheet } from "./Sheet";

export function UpdatePopup() {
  const { t } = useI18n();
  const user = useAuth((s) => s.user);
  const [unseen, setUnseen] = useState<ChangelogEntry[]>([]);
  const [version, setVersion] = useState("");

  useEffect(() => {
    if (!user) return;
    let alive = true;
    loadChangelog().then((d) => {
      if (!alive) return;
      setVersion(d.version);
      setUnseen(d.unseen);
    }).catch(() => {});
    return () => { alive = false; };
  }, [user?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
    setUnseen([]);
    void markSeen().catch(() => {});
  };

  return (
    <Sheet open={unseen.length > 0} onClose={close} title={t("changelog.updated", { version })} className="update-sheet"
           footer={<button type="button" className="mbtn primary" onClick={close}>{t("common.close")}</button>}>
      <ChangelogList entries={unseen} />
    </Sheet>
  );
}
