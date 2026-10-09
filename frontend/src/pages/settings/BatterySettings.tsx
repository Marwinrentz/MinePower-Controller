/** Einstellungen → Batterie (seit 2.16: ein Konzept).
 *
 *  Grundeinstellungen: EINE Reserve, 'Batterie zuerst bis …", Netzladen.
 *  Alles andere unter 'Experte". Jeder Regler hat Label, Einheit und einen
 *  Satz zur Wirkung; was das Gerät nicht kann, steht direkt daneben.
 */
import { useNavigate } from "react-router-dom";
import {
  Button, Callout, Card, CardHead, Disclosure, Field, NumberInput, Slider, ToggleRow,
} from "../../components/ui";
import { useI18n } from "../../i18n";
import { fmtW } from "../../lib/format";
import { useDevices } from "../../lib/useDevices";
import { useLive } from "../../store/live";
import { useSection } from "../../store/settings";
import { toast } from "../../store/toast";

export function BatterySettings() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const reg = useSection("regulation");
  const snapshot = useLive((s) => s.snapshot);
  const { devices, saveSettings } = useDevices();
  const live = (snapshot?.devices ?? []).filter((d) => d.category === "battery" && d.enabled);
  const stored = (devices ?? []).filter((d) => d.category === "battery");
  const controllable = live.some((d) => d.control_enabled !== false && d.control_capable !== false);
  const reserveMax = live.map((d) => d.reserve_max).find((v) => typeof v === "number") as number | undefined;

  const reserve = reg.num("battery_reserve_soc", 20);
  const priority = reg.num("battery_priority_soc", 100);
  const gridCharge = reg.bool("battery_grid_charge_enabled", false);
  const gridSoc = reg.num("battery_grid_charge_soc", 50);
  const houseLimit = reg.num("house_limit_a", 35);
  const housePhases = reg.num("house_phases", 3) === 1 ? 1 : 3;
  const manageReserve = stored.length === 0 || stored.every((d) => d.settings?.manage_reserve !== false);

  // Je Speicher still speichern, danach eine einzige Rückmeldung
  const setManageReserve = async (on: boolean) => {
    let ok = true;
    for (const d of stored) ok = (await saveSettings(d.id, { manage_reserve: on }, true)) !== false && ok;
    if (ok) toast.ok(t("common.saved"));
  };

  return (
    <>
      {live.length > 0 && !controllable && (
        <Callout tone="warn" action={<Button size="sm" onClick={() => navigate("/einstellungen?tab=devices")}>{t("set.battery.controlEnable")}</Button>}>
          {t("set.battery.controlOff")}
        </Callout>
      )}

      <Card>
        <CardHead title={t("set.battery.gBasic")} sub={t("set.battery.gBasicSub")} />
        <Slider label={t("set.battery.reserve")} value={reserve} min={0} max={80} step={5} tone="battery"
                format={(v) => `${v} %`} onChange={(v) => reg.set("battery_reserve_soc", v)}
                help={t("set.battery.reserveHelp")} />
        {reserveMax != null && reserve > reserveMax && (
          <p className="field-help strong">{t("set.battery.reserveSplit", { max: reserveMax, reserve })}</p>
        )}
        <Slider label={t("set.battery.priority")} value={priority} min={0} max={100} step={5} tone="battery"
                format={(v) => (v >= 100 ? t("set.battery.priorityAlways") : v <= 0 ? t("set.battery.priorityNever") : `${v} %`)}
                onChange={(v) => reg.set("battery_priority_soc", v)}
                marks={[{ value: 0, label: t("set.battery.priorityNever") }, { value: 50, label: "50 %" }, { value: 100, label: t("set.battery.priorityAlways") }]}
                help={t("set.battery.priorityHelp")} />
      </Card>

      <Card>
        <CardHead title={t("set.battery.gCheap")} />
        <ToggleRow label={t("set.battery.gridCharge")} help={t("set.battery.gridChargeHelp")}
                   on={gridCharge} onChange={(v) => reg.set("battery_grid_charge_enabled", v)} />
        {gridCharge && !controllable && <Callout tone="err">{t("set.battery.gridChargeWarn")}</Callout>}
        {gridCharge && (
          <>
            <Slider label={t("set.battery.gridSoc")} value={gridSoc} min={20} max={100} step={5} tone="battery"
                    format={(v) => `${v} %`} onChange={(v) => reg.set("battery_grid_charge_soc", v)}
                    help={t("set.battery.gridSocHelp")} />
            <ToggleRow label={t("set.battery.gridForecast")} help={t("set.battery.gridForecastHelp")}
                       on={reg.bool("battery_grid_charge_forecast", true)}
                       onChange={(v) => reg.set("battery_grid_charge_forecast", v)} />
          </>
        )}
        <p className="field-help">{t("set.battery.protectNote")}</p>
      </Card>

      <Card>
        <Disclosure title={t("set.expert")} summary={t("set.battery.expertSummary")}>
          <ToggleRow label={t("set.battery.manageReserve")} help={t("set.battery.manageReserveHelp")}
                     on={manageReserve} disabled={!controllable || stored.length === 0}
                     onChange={(v) => void setManageReserve(v)} />
          <ToggleRow label={t("set.battery.protectOther")} help={t("set.battery.protectOtherHelp")}
                     on={reg.bool("battery_protect_other_loads", false)}
                     onChange={(v) => reg.set("battery_protect_other_loads", v)} />
          <Slider label={t("set.battery.evSupport")} value={reg.num("battery_ev_support_soc", 0)} min={0} max={100} step={5}
                  tone="battery" format={(v) => (v <= 0 ? t("set.battery.never") : `ab ${v} %`)}
                  onChange={(v) => reg.set("battery_ev_support_soc", v)} help={t("set.battery.evSupportHelp")} />
          <div className="form-grid">
            <Field label={t("set.battery.gridMax")} help={t("set.battery.gridMaxHelp", { auto: fmtW(houseLimit * 230 * housePhases) })}>
              <NumberInput value={reg.num("grid_charge_max_w", 0)} onChange={(v) => reg.set("grid_charge_max_w", Math.max(0, v ?? 0))}
                           min={0} step={500} unit="W" />
            </Field>
            <Field label={t("set.battery.drainLimit")} help={t("set.battery.drainLimitHelp")}>
              <NumberInput value={reg.num("battery_drain_limit_w", 250)} onChange={(v) => reg.set("battery_drain_limit_w", v ?? 250)}
                           min={50} step={50} unit="W" />
            </Field>
            <Field label={t("set.battery.capacity")} help={t("set.battery.capacityHelp")}>
              <NumberInput value={reg.num("battery_capacity_kwh", 0) || null} onChange={(v) => reg.set("battery_capacity_kwh", Math.max(0, v ?? 0))}
                           min={0} step={0.5} unit="kWh" placeholder="–" />
            </Field>
            <Field label={t("set.battery.manualPower")} help={t("set.battery.manualPowerHelp")}>
              <NumberInput value={reg.num("battery_manual_power_w", 3000)} onChange={(v) => reg.set("battery_manual_power_w", v ?? 3000)}
                           min={100} step={100} unit="W" />
            </Field>
            <Field label={t("set.battery.manualMax")} help={t("set.battery.manualMaxHelp")}>
              <NumberInput value={reg.num("battery_manual_max_min", 240)} onChange={(v) => reg.set("battery_manual_max_min", v ?? 240)}
                           min={5} max={720} step={5} unit="min" />
            </Field>
          </div>
        </Disclosure>
      </Card>
    </>
  );
}
