/** Warmwasser – Speicher, Boost, Betriebsart.
 *
 *  Der Boost ist der häufigste Handgriff ('ich will jetzt duschen"): ein
 *  großer Knopf in der Mitte, auch im Querformat mit dem Daumen erreichbar,
 *  der sofort umschaltet und die Restzeit herunterzählt. Regler für
 *  Zieltemperatur, Höchstleistung und Wärmepuffer speichern beim Loslassen.
 */
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Boiler, type BoilerMark } from "../components/Boiler";
import { WaterDayChart } from "../components/charts";
import { DevicePicker } from "../components/DevicePicker";
import { ScheduleEditor } from "../components/ScheduleEditor";
import { Icon } from "../components/icons";
import {
  ActionButton, Badge, Button, Callout, Card, CardHead, Disclosure, Empty, Field, NumberInput, PageHead,
  Segment, Slider, useCountdown,
} from "../components/ui";
import { useI18n } from "../i18n";
import { confirmTouch } from "../components/Confirm";
import { waterSentence } from "../lib/explain";
import { fmtCt, fmtDuration, fmtTemp, fmtW } from "../lib/format";
import type { SnapshotDevice } from "../lib/types";
import { useDeviceCommand } from "../lib/useCommand";
import { useDevices } from "../lib/useDevices";
import { useDraft } from "../lib/useDraft";
import { useLive } from "../store/live";
import { boostPlan, boostTooHot } from "./Dashboard";

type WMode = "pv_only" | "pv_price" | "schedule" | "off";
const MODES: WMode[] = ["pv_only", "pv_price", "schedule", "off"];
const MODE_ICON = { pv_only: "sun", pv_price: "price", schedule: "clock", off: "stop" } as const;

function WaterDetail({ dev }: { dev: SnapshotDevice }) {
  const { t } = useI18n();
  const { devices, saveSettings } = useDevices();
  const snapshot = useLive((s) => s.snapshot);
  const stored = devices?.find((d) => d.id === dev.id);
  const saved = stored?.settings;
  const temp = typeof dev.data?.temperature_c === "number" ? (dev.data.temperature_c as number) : null;
  const power = Number(dev.data?.power ?? 0);
  const own = typeof dev.data?.device_mode === "string" && dev.data.device_mode ? (dev.data.device_mode as string) : null;
  const target = dev.target_temp_c ?? 60;
  const boostTemp = dev.boost_temp_c ?? 65;
  const maxPower = dev.max_power_w ?? 3000;
  // Nennleistung des Geräts (Konfiguration) – die Obergrenze des Reglers.
  const rated = Number(stored?.config?.rated_power ?? 0) || Math.max(maxPower, 3000);
  const endMode = dev.boost_end_mode ?? "both";

  const confirmed = useMemo(() => ({ mode: dev.mode ?? "pv_only", boost: dev.boost === true }), [dev.mode, dev.boost]);
  const cmd = useDeviceCommand(dev.id, confirmed);
  const mode = cmd.shown<string>("mode", confirmed.mode) as WMode;
  const boost = cmd.shown<boolean>("boost", confirmed.boost);
  const remaining = useCountdown(dev.boost ? dev.boost_state?.remaining_s : null);
  const boostPointless = !boost && boostTooHot(dev);

  // Regler: Anzeige folgt dem Finger, gespeichert wird beim Loslassen.
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const saveOne = async (key: string, value: unknown) => {
    setSavingKey(key);
    await saveSettings(dev.id, { [key]: value });
    setSavingKey(null);
  };
  const surplus = dev.surplus_temp_c ?? null;

  const s = waterSentence(dev, t);
  const status = own ? "own" : !dev.online ? "offline" : boost ? "boost" : power > 50 ? "heating"
    : mode === "off" ? "off" : temp != null && temp >= target ? "hot" : "standby";

  const marks: BoilerMark[] = [{ temp: target, label: `${t("heat.target")} ${fmtTemp(target)}`, kind: "target" }];
  if (boost || endMode !== "time") marks.push({ temp: boostTemp, label: `Boost ${fmtTemp(boostTemp)}`, kind: "boost" });
  if (surplus) marks.push({ temp: surplus, label: `☀ ${fmtTemp(surplus)}`, kind: "buffer" });

  const boostDraft = useDraft(saved);

  return (
    <>
      <Card className="heat-hero">
        <div className="heat-art">
          <Boiler temp={temp} power={power} maxPower={maxPower} marks={marks} own={!!own} label={t("heat.tank")} />
        </div>
        <div className="heat-main">
          <div className="row wrap tight">
            <Badge tone={status === "heating" || status === "boost" ? "ok" : status === "own" ? "warn" : status === "offline" ? "err" : "info"}>
              {t(`heat.state.${status}`)}
            </Badge>
            <span className="num heat-power">{fmtW(power)}</span>
          </div>
          {/* Läuft ein Boost, sagt die Boost-Karte darunter schon alles */}
          {!boost && <p className={`heat-status tone-${s.tone}`} aria-live="polite">{s.text}</p>}

          {/* Der große Boost-Knopf */}
          <div className={`boost-xl ${boost ? "on" : ""}`}>
            {boost ? (
              <>
                <div className="boost-xl-state" aria-live="polite">
                  <Icon name="boost" size={26} />
                  <div>
                    <strong>{t("heat.boostRunning")}</strong>
                    <span className="num">
                      {remaining != null && endMode !== "temp"
                        ? t("heat.boostRemaining", { time: fmtDuration(remaining) })
                        : t("heat.boostUntil", { temp: fmtTemp(dev.boost_state?.target_temp_c ?? boostTemp) })}
                    </span>
                  </div>
                </div>
                <Button size="xl" variant="danger" block loading={cmd.pending("boost")}
                        onClick={() => cmd.send("boost_off", undefined, { boost: false })}>
                  <Icon name="stop" size={20} /> {t("heat.boostStop")}
                </Button>
              </>
            ) : (
              <Button size="xl" variant="primary" block loading={cmd.pending("boost")}
                      disabled={!dev.online || boostPointless}
                      onClick={async () => {
                        if (await confirmTouch({ title: t("confirm.boostTitle"), icon: "boost", confirmLabel: t("heat.boostStart"),
                                                 text: t("confirm.boostText", { plan: boostPlan(dev, t) }) })) {
                          void cmd.send("boost", undefined, { boost: true });
                        }
                      }}>
                <Icon name="boost" size={24} />
                <span className="btn-text-2">
                  <span>{t("heat.boostStart")}</span>
                  <small>{boostPlan(dev, t)}</small>
                </span>
              </Button>
            )}
            <p className="field-help">{boostPointless ? t("heat.boostTooHot", { temp: fmtTemp(boostTemp) }) : t("heat.boostHelp")}</p>
          </div>
        </div>
      </Card>

      {own && (
        <p className="note-line">
          <Icon name="info" size={18} />
          <span>{t("heat.ownShort", { power: fmtW(power) })}</span>
          <Link to={`/einstellungen?tab=devices&edit=${dev.id}`} className="note-link">{t("common.details")}</Link>
        </p>
      )}

      <Card>
        <CardHead title={t("heat.mode")} />
        <Segment<WMode>
          size="lg"
          ariaLabel={t("heat.mode")}
          value={mode}
          onChange={(v) => void cmd.send("set_mode", v, { mode: v })}
          pending={cmd.pending("mode") ? mode : null}
          options={MODES.map((m) => ({ value: m, label: t(`modes.water.${m}`), icon: MODE_ICON[m] }))}
        />
        <p className="mode-help" aria-live="polite">{t(`modes.waterHelp.${mode}`)}</p>
        {mode === "schedule" && <ScheduleEditor deviceId={dev.id} />}
      </Card>

      <Card className="heat-sliders">
        <Slider
          label={t("heat.targetTemp")}
          help={t("heat.targetTempHelp")}
          value={target} min={35} max={75} step={1} tone="water"
          format={(v) => fmtTemp(v)}
          marks={[{ value: 40, label: "40" }, { value: 55, label: "55" }, { value: 60, label: "60" }, { value: 75, label: "75 °C" }]}
          disabled={savingKey === "target_temp_c"}
          onCommit={(v) => void saveOne("target_temp_c", v)}
        />
        <Slider
          label={t("heat.maxPower")}
          help={t("heat.maxPowerHelp")}
          value={Math.min(maxPower, rated)} min={300} max={rated} step={100} tone="water"
          format={(v) => fmtW(v)}
          disabled={savingKey === "max_power_w"}
          onCommit={(v) => void saveOne("max_power_w", v)}
        />
        <Slider
          label={t("heat.buffer")}
          help={t("heat.bufferHelp")}
          value={surplus ?? target} min={target} max={80} step={1} tone="pv"
          offLabel={t("heat.bufferOff")}
          format={(v) => fmtTemp(v)}
          disabled={savingKey === "surplus_temp_c"}
          onCommit={(v) => void saveOne("surplus_temp_c", v <= target ? null : v)}
        />
      </Card>

      <Card>
        <CardHead title={t("heat.day")} sub={t("heat.dayHelp")} />
        <WaterDayChart deviceId={dev.id} />
      </Card>

      <Card>
        <Disclosure title={t("heat.boostSettings")} summary={t("heat.boostSettingsSummary")}>
          <Field label={t("heat.boostEnd")}>
            <Segment
              size="lg"
              value={boostDraft.str("boost_end_mode", "both")}
              onChange={(v) => boostDraft.set("boost_end_mode", v)}
              options={[
                { value: "time", label: t("heat.endTime") },
                { value: "temp", label: t("heat.endTemp") },
                { value: "both", label: t("heat.endBoth") },
              ]}
            />
          </Field>
          <div className="form-grid">
            <Field label={t("heat.boostDuration")}>
              <NumberInput value={boostDraft.num("boost_duration_min", 60)} onChange={(v) => boostDraft.set("boost_duration_min", v)}
                           min={5} max={360} step={5} unit="min" />
            </Field>
            <Field label={t("heat.boostTemp")}>
              <NumberInput value={boostDraft.num("boost_temp_c", 65)} onChange={(v) => boostDraft.set("boost_temp_c", v)}
                           min={30} max={80} unit="°C" disabled={boostDraft.str("boost_end_mode", "both") === "time"} />
            </Field>
          </div>
          <p className="field-help">{t("heat.boostEndHelp")}</p>
          <div className="form-actions">
            {boostDraft.dirty && <Button variant="ghost" onClick={boostDraft.reset}>{t("set.discard")}</Button>}
            <ActionButton variant="primary" disabled={!boostDraft.dirty}
                          onClick={() => saveSettings(dev.id, {
                            boost_end_mode: boostDraft.draft.boost_end_mode,
                            boost_duration_min: boostDraft.draft.boost_duration_min,
                            boost_temp_c: boostDraft.draft.boost_temp_c,
                          })}>
              {t("set.save")}
            </ActionButton>
          </div>
        </Disclosure>
        <WaterAdvanced saved={saved} globalLimit={snapshot?.price_limit_ct ?? null}
                       onSave={(s) => saveSettings(dev.id, s)} />
      </Card>
    </>
  );
}

function WaterAdvanced({ saved, onSave, globalLimit }: {
  saved: Record<string, unknown> | undefined; onSave: (s: Record<string, unknown>) => Promise<boolean>;
  globalLimit: number | null;
}) {
  const { t } = useI18n();
  const d = useDraft(saved);
  return (
    <Disclosure title={t("heat.advanced")} summary={t("heat.advancedSummary")}>
      <Field label={t("heat.startThreshold")} help={t("heat.startThresholdHelp")}>
        <NumberInput value={d.num("start_threshold_w", 200)} onChange={(v) => d.set("start_threshold_w", v)}
                     min={0} step={50} unit="W" />
      </Field>
      <p className="field-help">
        {t("car.priceOne", { limit: globalLimit != null ? fmtCt(globalLimit) : "–" })}
      </p>
      <div className="form-actions">
        {d.dirty && <Button variant="ghost" onClick={d.reset}>{t("set.discard")}</Button>}
        <ActionButton variant="primary" disabled={!d.dirty} onClick={() => onSave(d.draft)}>{t("set.save")}</ActionButton>
      </div>
    </Disclosure>
  );
}

export function Water() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const snapshot = useLive((s) => s.snapshot);
  const [picked, setPicked] = useState<number | null>(null);
  if (!snapshot) return <div className="page"><div className="skeleton" style={{ height: 300 }} /></div>;
  const heaters = snapshot.devices.filter((d) => d.category === "water_heater" && d.enabled);
  const heater = heaters.find((h) => h.id === picked) ?? heaters[0];
  return (
    <div className="page">
      <PageHead title={t("heat.title")} sub={t("heat.sub")} />
      {!heater ? (
        <Card>
          <Empty icon={<Icon name="water" size={32} />} title={t("heat.none")}
                 action={<Button variant="primary" onClick={() => navigate("/einstellungen?tab=devices")}>{t("car.addDevice")}</Button>}>
            {t("heat.noneSub")}
          </Empty>
        </Card>
      ) : (
        <>
          <DevicePicker devices={heaters} active={heater.id} onPick={setPicked} />
          <WaterDetail key={heater.id} dev={heater} />
        </>
      )}
    </div>
  );
}
