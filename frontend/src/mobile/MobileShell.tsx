/** Handy-Gerüst: Inhalt + verwaschene Tab-Leiste unten.
 *
 *  Bewusst ohne Kopfleiste – jede Seite bringt ihren eigenen großen Titel mit,
 *  so wie man es von iOS kennt. Das spart eine Zeile Höhe und wirkt ruhiger.
 *
 *  **Vier Ziele plus „Mehr".** Mit sieben Einträgen blieben auf einem 375-px-
 *  Gerät je 53 px; „Warmwasser" und „Einstellungen" füllten ihre Zelle bis
 *  auf den letzten Pixel, ohne Luft nach beiden Seiten. Die Schrift noch
 *  kleiner zu setzen hätte die Lesbarkeit geopfert, um ein Platzproblem zu
 *  verstecken. Deshalb tragen unten nur die Ziele, die man im Alltag
 *  ansteuert; der Rest liegt hinter „Mehr". Das ist auch das Muster, das
 *  mobile Betriebssysteme selbst verwenden, sobald eine Leiste zu voll wird.
 */
import { Outlet, NavLink, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { useLive } from "../store/live";
import { Icon, type IconName } from "../components/icons";

type Tab = { to: string; icon: IconName; key: string };

/** Was man täglich ansteuert. */
const PRIMARY: Tab[] = [
  { to: "/", icon: "dashboard", key: "dashboard" },
  { to: "/vehicle", icon: "car", key: "vehicle" },
  { to: "/water", icon: "water", key: "water" },
  { to: "/devices", icon: "devices", key: "devices" },
];

/** Was man gelegentlich braucht. */
const SECONDARY: Tab[] = [
  { to: "/statistics", icon: "statistics", key: "statistics" },
  { to: "/settings", icon: "settings", key: "settings" },
  { to: "/events", icon: "events", key: "events" },
];

export function MobileShell() {
  const { t } = useI18n();
  const { connect, disconnect } = useLive();
  const { pathname } = useLocation();
  const [more, setMore] = useState(false);

  useEffect(() => {
    connect();
    return () => disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Beim Seitenwechsel schließt sich das Blatt von selbst – sonst bliebe es
  // über der neuen Seite stehen.
  useEffect(() => setMore(false), [pathname]);

  const inMore = SECONDARY.some((s) => pathname.startsWith(s.to));

  return (
    <div className="m-app">
      <Outlet />

      {more && (
        <>
          <button type="button" className="m-sheet-scrim" aria-label={t("common.close")}
                  onClick={() => setMore(false)} />
          <nav className="m-sheet" aria-label={t("nav.more")}>
            {SECONDARY.map((s) => (
              <NavLink key={s.to} to={s.to}
                       className={({ isActive }) => `m-sheet-item ${isActive ? "on" : ""}`}>
                <Icon name={s.icon} size={20} />
                {t(`nav.${s.key}`)}
              </NavLink>
            ))}
          </nav>
        </>
      )}

      <nav className="m-tabs" aria-label={t("nav.dashboard")}>
        {PRIMARY.map((tab) => (
          <NavLink key={tab.to} to={tab.to} end={tab.to === "/"}
                   className={({ isActive }) => `m-tab ${isActive ? "on" : ""}`}>
            <span className="m-tab-icon"><Icon name={tab.icon} size={22} /></span>
            {t(`nav.${tab.key}`)}
          </NavLink>
        ))}
        <button type="button" aria-expanded={more}
                className={`m-tab ${more || inMore ? "on" : ""}`}
                onClick={() => setMore((m) => !m)}>
          <span className="m-tab-icon"><Icon name="settings" size={22} /></span>
          {t("nav.more")}
        </button>
      </nav>
    </div>
  );
}
