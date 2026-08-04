/** Handy-Gerüst: Inhalt + verwaschene Tab-Leiste unten.
 *
 *  Bewusst ohne Kopfleiste – jede Seite bringt ihren eigenen großen Titel mit,
 *  so wie man es von iOS kennt. Das spart eine Zeile Höhe und wirkt ruhiger.
 */
import { Outlet, NavLink } from "react-router-dom";
import { useEffect } from "react";
import { useI18n } from "../i18n";
import { useLive } from "../store/live";
import { Icon, type IconName } from "../components/icons";

const TABS: { to: string; icon: IconName; key: string }[] = [
  { to: "/", icon: "dashboard", key: "dashboard" },
  { to: "/devices", icon: "devices", key: "devices" },
  { to: "/statistics", icon: "statistics", key: "statistics" },
  { to: "/settings", icon: "settings", key: "settings" },
  { to: "/events", icon: "events", key: "events" },
];

export function MobileShell() {
  const { t } = useI18n();
  const { connect, disconnect } = useLive();

  useEffect(() => {
    connect();
    return () => disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="m-app">
      <Outlet />
      <nav className="m-tabs" aria-label={t("nav.dashboard")}>
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} end={tab.to === "/"}
                   className={({ isActive }) => `m-tab ${isActive ? "on" : ""}`}>
            <span className="m-tab-icon"><Icon name={tab.icon} size={22} /></span>
            {t(`nav.${tab.key}`)}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
