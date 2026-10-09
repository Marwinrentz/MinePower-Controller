/** Login + Erst-Setup (Admin anlegen), je nach /api/system/setup-state. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Logo } from "../components/Logo";
import { Banner, Button, Card, Field } from "../components/ui";
import { useI18n } from "../i18n";
import { get, post } from "../lib/api";
import type { User } from "../lib/types";
import { useAuth } from "../store/auth";

type SetupState = { needs_admin: boolean; needs_devices: boolean };

export function Login() {
  const { t } = useI18n();
  const login = useAuth((s) => s.login);
  const navigate = useNavigate();
  const [setupState, setSetupState] = useState<SetupState | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<SetupState>("/api/system/setup-state").then(setSetupState).catch(() => setSetupState({ needs_admin: false, needs_devices: false }));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (setupState?.needs_admin) {
        const r = await post<{ token: string; user: User }>("/api/auth/setup", { email, password, name: name || "Admin" });
        login(r.token, r.user);
        navigate("/wizard");
      } else {
        const r = await post<{ token: string; user: User }>("/api/auth/login", { email, password });
        login(r.token, r.user);
        navigate(setupState?.needs_devices ? "/wizard" : "/");
      }
    } catch (err) {
      setError(String((err as Error).message) || t("login.failed"));
    } finally {
      setBusy(false);
    }
  };

  const isSetup = setupState?.needs_admin;

  return (
    <div className="login-page">
      <Card className="raised login-card">
        <div className="brand" style={{ justifyContent: "center", padding: "0 0 var(--s4)" }}>
          <span className="brand-mark" aria-hidden="true"><Logo size={34} /></span>
          MinePower
        </div>
        {setupState === null ? (
          <p className="dim">{t("common.loading")}</p>
        ) : (
          <form onSubmit={submit} className="col">
            {isSetup && (
              <>
                <h2>{t("login.setupTitle")}</h2>
                <p className="dim">{t("login.setupText")}</p>
                <Field label={t("login.name")}>
                  <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
                </Field>
              </>
            )}
            <Field label={t("login.email")}>
              <input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label={t("login.password")}>
              <input className="input" type="password" required minLength={isSetup ? 8 : 1}
                     value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            {error && <Banner tone="err" title={t("login.failed")}>{error}</Banner>}
            <Button type="submit" variant="primary" block disabled={busy}>
              {isSetup ? t("login.createAdmin") : t("login.submit")}
            </Button>
          </form>
        )}
      </Card>
    </div>
  );
}
