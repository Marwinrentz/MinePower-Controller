/** Rückkehrseite nach der Anmeldung beim Hersteller (/oauth/callback).
 *
 *  Tauscht selbst nichts: Sie gibt die Adresse (mit code und state) an das
 *  offene Geräteformular weiter, das den Vorgang gestartet hat, und schließt
 *  sich dann. Der Code allein nützt ohne den Vorgang im Server nichts. */
import { useEffect, useState } from "react";
import { OAUTH_CHANNEL } from "../components/SetupGuide";
import { useI18n } from "../i18n";

export function OAuthCallback() {
  const { t } = useI18n();
  const [sent, setSent] = useState(false);
  const params = new URLSearchParams(window.location.search);
  const error = params.get("error_description") || params.get("error");

  useEffect(() => {
    const state = params.get("state");
    if (!state || typeof BroadcastChannel === "undefined") return;
    const ch = new BroadcastChannel(OAUTH_CHANNEL);
    ch.postMessage({ url: window.location.href, state });
    setSent(true);
    ch.close();
    if (!error) {
      const timer = window.setTimeout(() => window.close(), 1500);
      return () => window.clearTimeout(timer);
    }
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="center-page">
      <div className="card" style={{ maxWidth: 480, margin: "15vh auto", padding: "var(--s6)" }}>
        <h2>{error ? t("device.setup.callbackError") : t("device.setup.callbackOk")}</h2>
        <p className="dim">
          {error ? error : sent ? t("device.setup.callbackClose") : t("device.setup.callbackPaste")}
        </p>
        {!error && !sent && (
          <input className="input" readOnly value={window.location.href} onFocus={(e) => e.target.select()} />
        )}
      </div>
    </div>
  );
}
