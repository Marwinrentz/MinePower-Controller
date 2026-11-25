/** Einrichtungshilfe im Geräteformular (seit 3.5):
 *
 *  - SetupGuide: Schritt-für-Schritt-Anleitung, woher Token, Passwort oder
 *    API-Schlüssel kommen. Links dürfen Platzhalter wie {url} oder {host}
 *    enthalten; sie werden mit den eingegebenen Werten gefüllt und erst
 *    gezeigt, wenn das Feld ausgefüllt ist.
 *  - OAuthConnect: Knopf 'Mit … verbinden'. Öffnet die Anmeldung des
 *    Herstellers, holt das Refresh-Token über /api/oauth und trägt es ins
 *    Formular ein. Zeigt die Redirect-URI auf MinePower (/oauth/callback),
 *    geht alles von selbst; sonst Adresse aus der Adresszeile einfügen.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { post } from "../lib/api";
import type { DriverMeta } from "../lib/types";
import { Icon } from "./icons";
import { Badge, Banner, Button, Field } from "./ui";

export const OAUTH_CHANNEL = "minepower-oauth";

const PROVIDERS: Record<string, { name: string; https: boolean }> = {
  viessmann: { name: "Viessmann", https: false },
  daikin_onecta: { name: "Daikin", https: true },
};

const MASK = "•••";

/** "{url}/profile/security" → mit Werten füllen; null, solange ein Wert fehlt. */
export function fillLink(link: string, config: Record<string, unknown>): string | null {
  let missing = false;
  const out = link.replace(/\{(\w+)\}/g, (_, key: string) => {
    const v = String(config[key] ?? "").trim().replace(/\/+$/, "");
    if (!v) missing = true;
    return v;
  });
  if (missing) return null;
  // Nur http(s): Ein eingetippter Wert darf keinen javascript:-Link erzeugen.
  if (/^https?:\/\/[^\s]+$/i.test(out)) return out;
  if (/^[\w.\-[\]:]+(\/[^\s]*)?$/.test(out)) return `http://${out}`;
  return null;
}

export function SetupGuide({ driver, config }: { driver: DriverMeta; config: Record<string, unknown> }) {
  const { t } = useI18n();
  const steps = driver.setup ?? [];
  if (!steps.length) return null;
  return (
    <div className="panel setup-guide">
      <span className="section-label"><Icon name="info" size={16} /> {t("device.setup.title")}</span>
      <ol className="setup-steps">
        {steps.map((s, i) => {
          const href = s.link ? fillLink(s.link, config) : null;
          return (
            <li key={i}>
              <span>{s.text}</span>
              {href && (
                <a className="setup-link" href={href} target="_blank" rel="noopener noreferrer">
                  {s.link_label ?? t("device.setup.open")} ↗
                </a>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function suggestedRedirect(https: boolean): string {
  const proto = https ? "https:" : window.location.protocol;
  return `${proto}//${window.location.host}/oauth/callback`;
}

async function copyText(text: string, input?: HTMLInputElement | null): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Ohne https gibt es keine Zwischenablage-API – markieren und klassisch kopieren.
    if (!input) return false;
    input.select();
    return document.execCommand("copy");
  }
}

export function OAuthConnect({ driver, config, deviceId, onToken }: {
  driver: DriverMeta;
  config: Record<string, unknown>;
  deviceId?: number | null;
  onToken: (field: string, token: string) => void;
}) {
  const { t } = useI18n();
  const provider = driver.oauth ? PROVIDERS[driver.oauth] : undefined;
  const storeKey = `minepower.oauth.redirect.${driver.oauth}`;
  const [redirect, setRedirect] = useState(() =>
    localStorage.getItem(storeKey) || suggestedRedirect(provider?.https ?? false));
  const [state, setState] = useState<string | null>(null);
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [copied, setCopied] = useState(false);
  const redirectRef = useRef<HTMLInputElement>(null);
  const stateRef = useRef<string | null>(null);
  stateRef.current = state;

  const hasToken = useMemo(() => {
    const v = String(config.refresh_token ?? "");
    return v.length > 0;
  }, [config.refresh_token]);

  const finish = async (codeOrUrl: string) => {
    setBusy(true);
    setError("");
    try {
      const res = await post<{ field: string; refresh_token: string; message: string }>("/api/oauth/finish", {
        state: stateRef.current, code_or_url: codeOrUrl,
      });
      onToken(res.field, res.refresh_token);
      setDone(res.message);
      setState(null);
      setAuthUrl(null);
      setPasted("");
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  // Rückmeldung der Seite /oauth/callback (anderer Tab, gleiche Adresse)
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const ch = new BroadcastChannel(OAUTH_CHANNEL);
    ch.onmessage = (ev: MessageEvent<{ url?: string; state?: string }>) => {
      const msg = ev.data ?? {};
      if (msg.url && msg.state && msg.state === stateRef.current) void finish(msg.url);
    };
    return () => ch.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!provider) return null;

  const clientId = String(config.client_id ?? "").trim();
  const secret = String(config.client_secret ?? "").trim();
  const needsSecret = driver.fields.some((f) => f.key === "client_secret");
  const redirectHost = (() => {
    try { return new URL(redirect).hostname; } catch { return ""; }
  })();
  const httpsProblem = provider.https && !redirect.startsWith("https://");
  const localhostProblem = provider.https && ["localhost", "127.0.0.1", "[::1]", "::1"].includes(redirectHost);

  const start = async () => {
    setError("");
    setDone("");
    localStorage.setItem(storeKey, redirect);
    // Fenster sofort öffnen (sonst blockiert der Browser das Popup nach dem await)
    const win = window.open("", "_blank");
    setBusy(true);
    try {
      const res = await post<{ auth_url: string; state: string }>("/api/oauth/start", {
        provider: driver.oauth, client_id: clientId, client_secret: secret,
        redirect_uri: redirect, device_id: deviceId ?? null,
      });
      setState(res.state);
      setAuthUrl(res.auth_url);
      if (win) win.location.href = res.auth_url;
    } catch (e) {
      win?.close();
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel oauth-connect">
      <div className="row between wrap tight">
        <span className="section-label">{t("device.setup.connectTitle", { name: provider.name })}</span>
        {hasToken && <Badge tone="ok">{t("device.setup.connected")}</Badge>}
      </div>

      <Field label={t("device.setup.redirect")} help={t(provider.https ? "device.setup.redirectHelpHttps"
                                                                       : "device.setup.redirectHelp")}>
        <div className="row tight">
          <input ref={redirectRef} className="input" value={redirect} spellCheck={false}
                 onChange={(e) => setRedirect(e.target.value.trim())} />
          <Button onClick={async () => setCopied(await copyText(redirect, redirectRef.current))}>
            {copied ? t("device.setup.copied") : t("device.setup.copy")}
          </Button>
        </div>
      </Field>
      {httpsProblem && <Banner tone="warn" title={t("device.setup.httpsNeeded", { name: provider.name })} />}
      {localhostProblem && <Banner tone="warn" title={t("device.setup.noLocalhost", { name: provider.name })} />}

      <div className="row wrap">
        <Button variant="primary" onClick={start}
                disabled={busy || !clientId || (needsSecret && !secret) || httpsProblem || localhostProblem}>
          {hasToken ? t("device.setup.reconnect", { name: provider.name })
                    : t("device.setup.connect", { name: provider.name })}
        </Button>
        {(!clientId || (needsSecret && !secret)) && (
          <small className="dim">{t(needsSecret ? "device.setup.needIdSecret" : "device.setup.needId")}</small>
        )}
        {authUrl && (
          <a className="setup-link" href={authUrl} target="_blank" rel="noopener noreferrer">
            {t("device.setup.openLogin")} ↗
          </a>
        )}
      </div>

      {state && (
        <Field label={t("device.setup.paste")} help={t("device.setup.pasteHelp")}>
          <div className="row tight">
            <input className="input" value={pasted} placeholder="https://…?code=…" spellCheck={false}
                   onChange={(e) => setPasted(e.target.value)} />
            <Button onClick={() => finish(pasted)} disabled={busy || !pasted.trim()}>
              {t("device.setup.apply")}
            </Button>
          </div>
        </Field>
      )}

      {done && <Banner tone="ok" title={done} />}
      {error && <Banner tone="err" title={t("common.error")}>{error}</Banner>}
      {hasToken && String(config.refresh_token) !== MASK && !done && (
        <small className="dim">{t("device.setup.tokenFilled")}</small>
      )}
    </div>
  );
}
