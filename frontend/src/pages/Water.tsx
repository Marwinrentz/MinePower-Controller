/** Seite „Warmwasser": Speicher im Detail mit Betriebsart und Boost. */
import { useState } from "react";
import { useI18n } from "../i18n";
import { useLive } from "../store/live";
import { DevicePicker, NoDevice, WaterDetail } from "../components/LoadDetail";

export function Water() {
  const { t } = useI18n();
  const snapshot = useLive((s) => s.snapshot);
  const [picked, setPicked] = useState<number | null>(null);

  if (!snapshot) return <p className="dim">{t("common.loading")}</p>;

  const heaters = snapshot.devices.filter((d) => d.category === "water_heater" && d.enabled);
  if (heaters.length === 0) return <NoDevice titleKey="water.none" subKey="water.noneSub" />;

  const heater = heaters.find((h) => h.id === picked) ?? heaters[0];
  return (
    <div className="ld-page">
      <DevicePicker devices={heaters} active={heater.id} onPick={setPicked} />
      <WaterDetail dev={heater} />
    </div>
  );
}
