/** Seite „Auto": ein Ladepunkt im Detail, darunter seine Ladezyklen.
 *
 *  Warmwasser hat eine eigene Seite. Beides untereinander hieß: Wer nach der
 *  Wassertemperatur sieht, scrollt jedes Mal an Fahrzeug, Reglern und dreißig
 *  Ladezyklen vorbei — und in der Navigation stand nur ein Eintrag für zwei
 *  Dinge, die nichts miteinander zu tun haben.
 */
import { useState } from "react";
import { useI18n } from "../i18n";
import { useLive } from "../store/live";
import { Cycles, DevicePicker, NoDevice, VehicleDetail } from "../components/LoadDetail";

export function Vehicle() {
  const { t } = useI18n();
  const snapshot = useLive((s) => s.snapshot);
  const [picked, setPicked] = useState<number | null>(null);

  if (!snapshot) return <p className="dim">{t("common.loading")}</p>;

  const cars = snapshot.devices.filter((d) => d.category === "wallbox" && d.enabled);
  if (cars.length === 0) return <NoDevice titleKey="vehicle.none" subKey="vehicle.noneSub" />;

  const car = cars.find((c) => c.id === picked) ?? cars[0];
  return (
    <div className="ld-page">
      <DevicePicker devices={cars} active={car.id} onPick={setPicked} />
      <VehicleDetail dev={car} />
      <Cycles deviceId={car.id} />
    </div>
  );
}
