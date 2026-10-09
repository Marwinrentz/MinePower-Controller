/** App-Rahmen – eine Oberfläche für alle Bildschirme.
 *
 *  • Breit (Desktop, Tablet quer): Seitenleiste links.
 *  • Handy hochkant: Tab-Leiste unten in der Daumenzone (vier Ziele + 'Mehr").
 *  • Handy quer: schmale Symbolleiste links – eine Leiste unten würde dort ein
 *    Viertel der ohnehin knappen Höhe kosten.
 *
 *  Vorher gab es zwei getrennte Oberflächen (Desktop und Handy) mit doppelter
 *  Logik; Funktionen kamen dadurch mal nur in der einen an (etwa die
 *  Batterie-Knöpfe). Jetzt teilen sich alle Größen dieselben Seiten, und nur
 *  die Navigation wechselt per CSS.
 */
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useI18n } from "../i18n";
import { APP_VERSION, useServerVersion } from "../lib/useVersion";
import { useAuth } from "../store/auth";
import { useLive } from "../store/live";
import { useTheme, type ThemeMode } from "../store/theme";
import { GITHUB_URL } from "../lib/links";
import { useToasts } from "../store/toast";
import { Icon, type IconName } from "./icons";
import { ConfirmHost } from "./Confirm";
import { Logo } from "./Logo";
import { Sheet } from "./Sheet";
import { UpdatePopup } from "./UpdatePopup";

type NavItem = { to: string; icon: IconName; key: string; primary?: boolean };

const NAV: NavItem[] = [
  { to: "/", icon: "dashboard", key: "dashboard", primary: true },
  { to: "/auto", icon: "car", key: "vehicle", primary: true },
  { to: "/warmwasser", icon: "water", key: "water", primary: true },
  { to: "/batterie", icon: "battery", key: "battery", primary: true },
  { to: "/tarif", icon: "price", key: "tariff" },
  { to: "/statistik", icon: "statistics", key: "statistics" },
  { to: "/diagnose", icon: "pulse", key: "diagnose" },
  { to: "/einstellungen", icon: "settings", key: "settings" },
  { to: "/changelog", icon: "events", key: "changelog" },
];

function LiveDot({ connected, stale }: { connected: boolean; stale: boolean }) {
  const { t } = useI18n();
  const ok = connected && !stale;
  return (
    <span className={`live ${ok ? "ok" : "off"}`} role="status" aria-live="polite">
      <span className={`dot ${ok ? "online" : "offline"}`} />
      <span className="live-label">{ok ? t("shell.live") : t("shell.connecting")}</span>
    </span>
  );
}

function ThemeButton({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  const { mode, setMode } = useTheme();
  const next: Record<ThemeMode, ThemeMode> = { dark: "light", light: "system", system: "dark" };
  const icon: IconName = mode === "dark" ? "moon" : mode === "light" ? "sun" : "display";
  const label = t(mode === "dark" ? "shell.themeDark" : mode === "light" ? "shell.themeLight" : "shell.themeSystem");
  return (
    <button type="button" className={compact ? "btn ghost icon" : "btn ghost"} onClick={() => setMode(next[mode])}
            title={`${t("shell.theme")}: ${label}`} aria-label={`${t("shell.theme")}: ${label}`}>
      <Icon name={icon} size={18} />
      {!compact && <span>{label}</span>}
    </button>
  );
}

function Toaster() {
  const toasts = useToasts((s) => s.toasts);
  const dismiss = useToasts((s) => s.dismiss);
  return (
    <div className="toaster" aria-live="polite" aria-atomic="false">
      {toasts.map((t) => t.action ? (
        <div key={t.id} className={`toast ${t.tone} has-action`} role="status">
          <Icon name="check" size={18} />
          <span>{t.text}</span>
          <button type="button" className="toast-action" onClick={() => { t.action?.run(); dismiss(t.id); }}>
            {t.action.label}
          </button>
        </div>
      ) : (
        <button key={t.id} type="button" className={`toast ${t.tone}`} onClick={() => dismiss(t.id)}>
          <Icon name={t.tone === "err" ? "warning" : t.tone === "ok" ? "check" : "info"} size={18} />
          <span>{t.text}</span>
        </button>
      ))}
    </div>
  );
}

function MoreSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, lang, setLang } = useI18n();
  const { logout, user } = useAuth();
  const navigate = useNavigate();
  return (
    <Sheet open={open} onClose={onClose} label={t("nav.more")} className="more-sheet">
      <nav className="sheet-grid" aria-label={t("nav.more")}>
        {NAV.filter((n) => !n.primary).map((n) => (
          <NavLink key={n.to} to={n.to} className="sheet-item" onClick={onClose}>
            <Icon name={n.icon} size={22} />
            <span>{t(`nav.${n.key}`)}</span>
          </NavLink>
        ))}
        <button type="button" className="sheet-item" onClick={() => { onClose(); navigate("/wall"); }}>
          <Icon name="fullscreen" size={22} />
          <span>{t("nav.wall")}</span>
        </button>
      </nav>
      <div className="sheet-row">
        <ThemeButton />
        <button type="button" className="btn ghost" onClick={() => setLang(lang === "de" ? "en" : "de")}>
          <Icon name="display" size={18} /> {lang === "de" ? "English" : "Deutsch"}
        </button>
        <button type="button" className="btn ghost" onClick={() => { onClose(); logout(); }} title={user?.email}>
          <Icon name="power" size={18} /> {t("nav.logout")}
        </button>
        <a className="btn ghost" href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
          <Icon name="github" size={18} /> {t("shell.github")}
        </a>
      </div>
      <p className="sheet-version">{t("shell.version", { version: APP_VERSION })}</p>
    </Sheet>
  );
}

export function AppShell() {
  const { t, lang, setLang } = useI18n();
  const { user, logout } = useAuth();
  const connect = useLive((s) => s.connect);
  const disconnect = useLive((s) => s.disconnect);
  const connected = useLive((s) => s.connected);
  const stale = useLive((s) => s.stale);
  const { server, outdated } = useServerVersion();
  const [more, setMore] = useState(false);
  const location = useLocation();

  useEffect(() => {
    connect();
    return () => disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Beim Seitenwechsel nach oben – sonst landet man auf einer neuen Seite
  // mitten im Inhalt, weil die alte weit nach unten gescrollt war.
  useEffect(() => {
    window.scrollTo({ top: 0 });
    setMore(false);
  }, [location.pathname]);

  const moreActive = NAV.some((n) => !n.primary && location.pathname.startsWith(n.to));

  return (
    <div className="shell">
      <a className="skip-link" href="#main">{t("shell.skip")}</a>

      {/* Seitenleiste (breit) und Symbolleiste (Handy quer) – per CSS umgeschaltet */}
      <aside className="sidebar" aria-label={t("nav.main")}>
        <div className="brand">
          <Logo size={28} />
          <span className="brand-name">MinePower</span>
        </div>
        <nav className="side-nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
                     className={({ isActive }) => `side-item ${isActive ? "active" : ""}`}
                     title={t(`nav.${n.key}`)}>
              <Icon name={n.icon} size={20} />
              <span className="side-label">{t(`nav.${n.key}`)}</span>
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <LiveDot connected={connected} stale={stale} />
          <div className="side-tools">
            <ThemeButton compact />
            <button type="button" className="btn ghost icon" onClick={() => setLang(lang === "de" ? "en" : "de")}
                    title={t("shell.language")} aria-label={t("shell.language")}>
              <span className="lang-code">{lang.toUpperCase()}</span>
            </button>
            <button type="button" className="btn ghost icon" onClick={logout}
                    title={`${t("nav.logout")} (${user?.email ?? ""})`} aria-label={t("nav.logout")}>
              <Icon name="power" size={18} />
            </button>
          </div>
          <a className="side-link" href={GITHUB_URL} target="_blank" rel="noopener noreferrer"
             title={t("shell.github")} aria-label={t("shell.github")}>
            <Icon name="github" size={18} />
            <span className="side-label">{t("shell.github")}</span>
          </a>
          <span className="side-version">{APP_VERSION}</span>
        </div>
      </aside>

      {/* Kopfzeile (Handy hochkant) */}
      <header className="topbar">
        <div className="brand">
          <Logo size={24} />
          <span className="brand-name">MinePower</span>
        </div>
        <LiveDot connected={connected} stale={stale} />
      </header>

      <main id="main" className="main" tabIndex={-1}>
        {outdated && (
          <div className="banner info update-banner" role="status">
            <span className="banner-icon"><Icon name="refresh" size={18} /></span>
            <div className="banner-text">
              <strong>{t("shell.updateTitle")}</strong>
              <span>{t("shell.updateText", { version: server ?? "" })}</span>
            </div>
            <div className="banner-actions">
              <button type="button" className="btn primary btn-sm" onClick={() => window.location.reload()}>
                {t("shell.reload")}
              </button>
            </div>
          </div>
        )}
        {!connected && (
          <div className="banner warn conn-banner" role="status">
            <span className="banner-icon"><Icon name="warning" size={18} /></span>
            <div className="banner-text">
              <strong>{t("shell.offline")}</strong>
              <span>{t("shell.offlineText")}</span>
            </div>
          </div>
        )}
        <Outlet />
      </main>

      {/* Tab-Leiste (Handy hochkant) */}
      <nav className="tabbar" aria-label={t("nav.main")}>
        {NAV.filter((n) => n.primary).map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === "/"}
                   className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>
            <Icon name={n.icon} size={22} />
            <span>{t(`nav.${n.key}`)}</span>
          </NavLink>
        ))}
        <button type="button" className={`tab ${moreActive ? "active" : ""}`} aria-expanded={more}
                onClick={() => setMore(true)}>
          <Icon name="more" size={22} />
          <span>{t("nav.more")}</span>
        </button>
      </nav>

      <MoreSheet open={more} onClose={() => setMore(false)} />
      <ConfirmHost />
      <UpdatePopup />
      <Toaster />
    </div>
  );
}
