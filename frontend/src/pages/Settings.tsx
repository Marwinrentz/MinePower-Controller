/** Einstellungen – in Bereiche geteilt statt einer langen Seite.
 *
 *  Der gewählte Bereich steht in der Adresse (?tab=battery), damit Links von
 *  anderen Seiten ('Batterie-Einstellungen") direkt dorthin führen.
 *  Ungespeicherte Änderungen zeigt die Leiste am unteren Rand.
 */
import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { Icon, type IconName } from "../components/icons";
import { SaveBar } from "../components/SaveBar";
import { PageHead } from "../components/ui";
import { useI18n } from "../i18n";
import { useAuth } from "../store/auth";
import { useSettings } from "../store/settings";
import { BatterySettings } from "./settings/BatterySettings";
import { DevicesPanel } from "./settings/DevicesPanel";
import {
  AppearanceSettings, BackupSettings, InfoSettings, NotificationSettings, PrioritySettings, UsersSettings,
} from "./settings/OtherSettings";
import { RegulationSettings } from "./settings/RegulationSettings";

type Tab = "regulation" | "battery" | "priority" | "devices" | "notifications" | "users" | "appearance" | "backup" | "info";

const TABS: { key: Tab; icon: IconName; admin?: boolean }[] = [
  { key: "regulation", icon: "auto" },
  { key: "battery", icon: "battery" },
  { key: "priority", icon: "events" },
  { key: "devices", icon: "devices" },
  { key: "notifications", icon: "info" },
  { key: "users", icon: "lock", admin: true },
  { key: "appearance", icon: "display" },
  { key: "backup", icon: "download", admin: true },
  { key: "info", icon: "info" },
];

export function Settings() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  const loaded = useSettings((s) => s.loaded);
  const load = useSettings((s) => s.load);
  useEffect(() => { void load(); }, [load]);

  const tabs = TABS.filter((x) => !x.admin || isAdmin);
  const tab = (tabs.find((x) => x.key === params.get("tab"))?.key ?? "regulation") as Tab;
  // Gewählten Bereich in der wischbaren Leiste sichtbar halten (Handy)
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => {
    navRef.current?.querySelector(".active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [tab]);

  return (
    <div className="page settings">
      <PageHead title={t("set.title")} sub={t("set.sub")} />
      <nav ref={navRef} className="subnav" aria-label={t("set.title")}>
        {tabs.map((x) => (
          <button key={x.key} type="button" className={`subnav-item ${tab === x.key ? "active" : ""}`}
                  aria-current={tab === x.key ? "page" : undefined}
                  onClick={() => setParams({ tab: x.key }, { replace: true })}>
            <Icon name={x.icon} size={18} />
            <span>{t(`set.sec.${x.key}`)}</span>
          </button>
        ))}
      </nav>
      {!loaded && tab !== "devices" ? <div className="skeleton" style={{ height: 300 }} /> : (
        <div className="settings-body">
          {tab === "regulation" && <RegulationSettings />}
          {tab === "battery" && <BatterySettings />}
          {tab === "priority" && <PrioritySettings />}
          {tab === "devices" && <DevicesPanel />}
          {tab === "notifications" && <NotificationSettings />}
          {tab === "users" && <UsersSettings />}
          {tab === "appearance" && <AppearanceSettings />}
          {tab === "backup" && <BackupSettings />}
          {tab === "info" && <InfoSettings />}
        </div>
      )}
      <SaveBar />
    </div>
  );
}
