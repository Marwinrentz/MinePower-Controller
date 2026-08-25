/** App-Rahmen: Seitenleiste am Desktop, Tab-Leiste am Handy.
 *
 *  Am Handy liegt die Navigation unten in der Daumenzone – dort, wo man sie
 *  im Vorbeigehen bedient („lädt das Auto gerade?“). Die Kopfzeile bleibt
 *  schmal und zeigt nur, ob die Live-Verbindung steht.
 */
import { useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useI18n } from "../i18n";
import { Icon, type IconName } from "./icons";
import { Logo } from "./Logo";
import { useAuth } from "../store/auth";
import { useLive } from "../store/live";
import { useTheme } from "../store/theme";

const NAV: { to: string; icon: IconName; key: string }[] = [
  { to: "/", icon: "dashboard", key: "dashboard" },
  { to: "/vehicle", icon: "car", key: "vehicle" },
  { to: "/water", icon: "water", key: "water" },
  { to: "/devices", icon: "devices", key: "devices" },
  { to: "/statistics", icon: "statistics", key: "statistics" },
  { to: "/settings", icon: "settings", key: "settings" },
  { to: "/events", icon: "events", key: "events" },
];

function LiveIndicator({ connected, label }: { connected: boolean; label: string }) {
  return (
    <span className="row tight" style={{ fontSize: "var(--text-xs)", color: "var(--text-dim)" }}>
      <span className={`dot ${connected ? "online" : "offline"}`} />
      {connected ? "Live" : label}
    </span>
  );
}

export function Layout() {
  const { t, lang, setLang } = useI18n();
  const { mode, setMode } = useTheme();
  const { user, logout } = useAuth();
  const { connect, disconnect, connected } = useLive();

  useEffect(() => {
    connect();
    return () => disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const themeIcon: IconName = mode === "dark" ? "moon" : mode === "light" ? "sun" : "display";
  const cycleTheme = () => setMode(mode === "dark" ? "light" : mode === "light" ? "system" : "dark");

  return (
    <div className="app-shell">
      <nav className="sidebar" aria-label={t("nav.dashboard")}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true"><Logo size={26} /></span>
          MinePower
        </div>
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.to === "/"}
            className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
          >
            <span className="nav-icon"><Icon name={n.icon} size={18} /></span>
            {t(`nav.${n.key}`)}
          </NavLink>
        ))}

        <div className="spacer" />

        <div className="row tight" style={{ padding: "0 var(--s3)" }}>
          <button className="btn small icon" onClick={cycleTheme} title={t("settings.theme")}>
            <Icon name={themeIcon} size={16} />
          </button>
          <button className="btn small" onClick={() => setLang(lang === "de" ? "en" : "de")}
                  title={t("settings.language")}>
            {lang.toUpperCase()}
          </button>
        </div>
        <div className="nav-item" onClick={logout} title={user?.email} role="button" tabIndex={0}
             onKeyDown={(e) => e.key === "Enter" && logout()}>
          <span className="nav-icon"><Icon name="power" size={18} /></span> {t("nav.logout")}
        </div>
        <div style={{ padding: "var(--s2) var(--s3) 0" }}>
          <LiveIndicator connected={connected} label={t("common.connecting")} />
        </div>
      </nav>

      {/* Kopfzeile nur mobil (per CSS eingeblendet) */}
      <header className="topbar">
        <div className="brand" style={{ padding: 0, fontSize: "var(--text-md)" }}>
          <span className="brand-mark" aria-hidden="true"><Logo size={22} /></span>
          MinePower
        </div>
        <div className="row tight">
          <LiveIndicator connected={connected} label={t("common.connecting")} />
          <button className="btn small icon ghost" onClick={cycleTheme} title={t("settings.theme")}>
            <Icon name={themeIcon} size={16} />
          </button>
          <button className="btn small icon ghost" onClick={logout} title={t("nav.logout")}><Icon name="power" size={16} /></button>
        </div>
      </header>

      <main className="main-content">
        <Outlet />
      </main>

      <nav className="tabbar" aria-label={t("nav.dashboard")}>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === "/"}
                   className={({ isActive }) => `tab-item ${isActive ? "active" : ""}`}>
            <span className="nav-icon"><Icon name={n.icon} size={18} /></span>
            {t(`nav.${n.key}`)}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
