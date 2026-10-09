/** Einstellungen → Regelung.
 *
 *  Grund: Takt und Hausanschluss. Experte: Netz-Sollwert, Wecken, Prognose,
 *  wie lange Eingriffe am Auto höchstens gelten. Totband und Glättung sind
 *  seit 2.16 keine Einstellung mehr – sie passen sich der Wetterlage an und
 *  werden hier nur angezeigt.
 */
import { Card, CardHead, Disclosure, Field, NumberInput, Segment, ToggleRow } from "../../components/ui";
import { useI18n } from "../../i18n";
import { fmtW } from "../../lib/format";
import { useLive } from "../../store/live";
import { useSection } from "../../store/settings";

const INTERVALS = ["5", "10", "20"];

export function RegulationSettings() {
  const { t } = useI18n();
  const reg = useSection("regulation");
  const forecast = useSection("forecast");
  const snapshot = useLive((s) => s.snapshot);
  const interval = String(Math.round(reg.num("interval_s", 10)));

  return (
    <>
      <Card>
        <CardHead title={t("set.regulation.basics")} />
        <Field label={t("set.regulation.interval")} help={t("set.regulation.intervalHelp")}>
          <Segment size="lg" value={INTERVALS.includes(interval) ? interval : "10"}
                   onChange={(v) => reg.set("interval_s", Number(v))}
                   options={INTERVALS.map((v) => ({ value: v, label: `${v} s`, hint: v === "10" ? t("set.regulation.recommended") : undefined }))} />
        </Field>
        <Field label={t("set.regulation.houseLimit")} help={t("set.regulation.houseLimitHelp")}>
          <NumberInput value={reg.num("house_limit_a", 35)} onChange={(v) => reg.set("house_limit_a", v ?? 35)} min={6} max={250} unit="A" />
        </Field>
        <Field label={t("set.regulation.housePhases")}>
          <Segment value={String(reg.num("house_phases", 3))} onChange={(v) => reg.set("house_phases", Number(v))}
                   options={[{ value: "1", label: "1" }, { value: "3", label: "3" }]} ariaLabel={t("set.regulation.housePhases")} />
        </Field>
        <p className="field-help">
          {t("set.regulation.autoTune", { band: fmtW(snapshot?.deadband_w ?? 100), weather: snapshot?.weather ?? "–" })}
        </p>
      </Card>

      <Card>
        <CardHead title={t("set.regulation.sun")} />
        <ToggleRow label={t("set.regulation.forecast")} help={t("set.regulation.forecastHelp")}
                   on={reg.bool("use_forecast", true)} onChange={(v) => reg.set("use_forecast", v)} />
        <Field label={t("set.regulation.forecastProvider")} help={t("set.regulation.forecastProviderHelp")}>
          <Segment size="lg" value={forecast.str("provider", "none")} onChange={(v) => forecast.set("provider", v)}
                   options={[{ value: "none", label: t("set.regulation.none") }, { value: "forecast_solar", label: "forecast.solar" }]} />
        </Field>
        {forecast.str("provider", "none") === "forecast_solar" && (
          <div className="form-grid">
            <Field label={t("set.regulation.lat")}>
              <NumberInput value={forecast.num("lat", 0)} onChange={(v) => forecast.set("lat", v)} step={0.0001} unit="°" />
            </Field>
            <Field label={t("set.regulation.lon")}>
              <NumberInput value={forecast.num("lon", 0)} onChange={(v) => forecast.set("lon", v)} step={0.0001} unit="°" />
            </Field>
            <Field label={t("set.regulation.kwp")}>
              <NumberInput value={forecast.num("kwp", 0)} onChange={(v) => forecast.set("kwp", v)} step={0.1} unit="kWp" />
            </Field>
            <Field label={t("set.regulation.declination")}>
              <NumberInput value={forecast.num("declination", 30)} onChange={(v) => forecast.set("declination", v)} unit="°" />
            </Field>
          </div>
        )}
      </Card>

      <Card>
        <Disclosure title={t("set.expert")} summary={t("set.regulation.expertSummary")}>
          <Field label={t("set.regulation.gridTarget")} help={t("set.regulation.gridTargetHelp")}>
            <NumberInput value={reg.num("grid_target_w", 0)} onChange={(v) => reg.set("grid_target_w", v ?? 0)} step={10} unit="W" />
          </Field>
          <ToggleRow label={t("set.regulation.wake")} help={t("set.regulation.wakeHelp")}
                     on={reg.bool("vehicle_wake_enabled", true)} onChange={(v) => reg.set("vehicle_wake_enabled", v)} />
          <div className="form-grid">
            <Field label={t("set.regulation.stopHours")} help={t("set.regulation.stopHoursHelp")}>
              <NumberInput value={reg.num("override_stop_h", 4)} onChange={(v) => reg.set("override_stop_h", v ?? 4)} min={0.5} max={48} step={0.5} unit="h" />
            </Field>
            <Field label={t("set.regulation.fastHours")} help={t("set.regulation.fastHoursHelp")}>
              <NumberInput value={reg.num("override_fast_h", 12)} onChange={(v) => reg.set("override_fast_h", v ?? 12)} min={0.5} max={48} step={0.5} unit="h" />
            </Field>
          </div>
        </Disclosure>
      </Card>
    </>
  );
}
