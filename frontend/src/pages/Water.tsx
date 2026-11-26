/** Warmwasser – Speicher, Boost, Betriebsart.
 *
 *  Der Boost ist der häufigste Handgriff ('ich will jetzt duschen"): ein
 *  großer Knopf in der Mitte, auch im Querformat mit dem Daumen erreichbar,
 *  der sofort umschaltet und die Restzeit herunterzählt. Regler für
 *  Zieltemperatur, Höchstleistung und Wärmepuffer speichern beim Loslassen.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Boiler, type BoilerMark } from "../components/Boiler";
import { WaterDayChart } from "../components/charts";
import { ClimateArt, HeatPumpArt } from "../components/FlowArt";
import { ScheduleEditor } from "../components/ScheduleEditor";
import { Icon } from "../components/icons";
import {
  ActionButton, Badge, Button, Callout, Card, CardHead, Disclosure, Empty, Field, NumberInput, PageHead,
  Segment, Slider, ToggleRow, useCountdown,
} from "../components/ui";
import { useI18n } from "../i18n";
import { confirmTouch } from "../components/Confirm";
import { heatSentence, waterSentence } from "../lib/explain";
import { HEAT_ICON, HEAT_LABEL, HEAT_TONE, devPower, devTemp, heatKind, type HeatKind } from "../lib/heat";
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

/* ------------------------------------------------------------ Wärmepumpe, Klima */

type HMode = WMode;

/** Wärmepumpe oder Klimaanlage: regelt selbst, MinePower gibt frei. Statt
 *  Speicher und Boost-Temperatur zeigt die Karte das Gerät, den Zustand der
 *  Freigabe und den Verdichterschutz. */
function HeatPumpDetail({ dev }: { dev: SnapshotDevice }) {
  const { t } = useI18n();
  const { devices, saveSettings } = useDevices();
  const snapshot = useLive((s) => s.snapshot);
  const stored = devices?.find((d) => d.id === dev.id);
  const saved = stored?.settings;
  const kind = heatKind(dev);
  const temp = devTemp(dev);
  const power = devPower(dev);
  const released = Number((dev.decision as { power_w?: number } | undefined)?.power_w ?? 0) > 0;

  const confirmed = useMemo(() => ({ mode: dev.mode ?? "pv_only", boost: dev.boost === true }), [dev.mode, dev.boost]);
  const cmd = useDeviceCommand(dev.id, confirmed);
  const mode = cmd.shown<string>("mode", confirmed.mode) as HMode;
  const boost = cmd.shown<boolean>("boost", confirmed.boost);
  const remaining = useCountdown(dev.boost ? dev.boost_state?.remaining_s : null);
  const s = heatSentence(dev, t);
  const status = !dev.online ? "offline" : boost || released ? "released" : power > 50 ? "normal" : "waiting";
  const plan = t("heat.releasePlan", { time: fmtDuration((dev.boost_duration_min ?? 60) * 60) });

  const d = useDraft(saved);
  const minOn = Math.round(d.num("min_on_s", dev.min_on_s ?? 600) / 60);
  const minOff = Math.round(d.num("min_off_s", dev.min_off_s ?? 300) / 60);

  return (
    <>
      <Card className="heat-hero hp-hero">
        <div className="hp-art">
          {kind === "climate" ? <ClimateArt active={power > 50} /> : <HeatPumpArt active={power > 50} temp={temp} />}
        </div>
        <div className="heat-main">
          <div className="row wrap tight">
            <Badge tone={status === "released" ? "ok" : status === "offline" ? "err" : "info"}>
              {t(`heatState.${status}`)}
            </Badge>
            <span className="num heat-power">{fmtW(power)}</span>
            {temp != null && <span className="num hp-temp"><Icon name="thermo" size={16} /> {fmtTemp(temp)}</span>}
          </div>
          <p className={`heat-status tone-${s.tone}`} aria-live="polite">{s.text}</p>
          <div className={`boost-xl ${boost ? "on" : ""}`}>
            {boost ? (
              <>
                <div className="boost-xl-state" aria-live="polite">
                  <Icon name="boost" size={26} />
                  <div>
                    <strong>{t("heat.releaseRunning")}</strong>
                    <span className="num">{remaining != null ? t("heat.boostRemaining", { time: fmtDuration(remaining) }) : ""}</span>
                  </div>
                </div>
                <Button size="xl" variant="danger" block loading={cmd.pending("boost")}
                        onClick={() => cmd.send("boost_off", undefined, { boost: false })}>
                  <Icon name="stop" size={20} /> {t("heat.releaseStop")}
                </Button>
              </>
            ) : (
              <Button size="xl" variant="primary" block loading={cmd.pending("boost")} disabled={!dev.online || mode === "off"}
                      onClick={async () => {
                        if (await confirmTouch({ title: t("heat.releaseStart"), icon: "boost", confirmLabel: t("heat.releaseStart"),
                                                 text: `${t("heat.releaseHelp")} (${plan})` })) {
                          void cmd.send("boost", undefined, { boost: true });
                        }
                      }}>
                <Icon name="boost" size={24} />
                <span className="btn-text-2">
                  <span>{t("heat.releaseStart")}</span>
                  <small>{plan}</small>
                </span>
              </Button>
            )}
            <p className="field-help">{t("heat.releaseHelp")}</p>
          </div>
        </div>
      </Card>

      <Card>
        <CardHead title={t("heat.mode")} />
        <Segment<HMode>
          size="lg"
          ariaLabel={t("heat.mode")}
          value={mode}
          onChange={(v) => void cmd.send("set_mode", v, { mode: v })}
          pending={cmd.pending("mode") ? mode : null}
          options={MODES.map((m) => ({ value: m, label: t(`modes.water.${m}`), icon: MODE_ICON[m] }))}
        />
        {mode === "schedule" && <ScheduleEditor deviceId={dev.id} />}
      </Card>

      <Card>
        <CardHead title={t("heat.day")} sub={t("heat.dayHelp")} />
        <WaterDayChart deviceId={dev.id} />
      </Card>

      <Card>
        <Disclosure title={t("heat.compressor")} summary={t("heat.compressorSummary")}>
          <div className="form-grid">
            <Field label={t("heat.minOn")}>
              <NumberInput value={minOn} onChange={(v) => d.set("min_on_s", Math.max(0, Math.min(120, v ?? 0)) * 60)}
                           min={0} max={120} step={1} unit="min" />
            </Field>
            <Field label={t("heat.minOff")}>
              <NumberInput value={minOff} onChange={(v) => d.set("min_off_s", Math.max(0, Math.min(120, v ?? 0)) * 60)}
                           min={0} max={120} step={1} unit="min" />
            </Field>
            <Field label={t("heat.boostDuration")}>
              <NumberInput value={d.num("boost_duration_min", dev.boost_duration_min ?? 60)}
                           onChange={(v) => d.set("boost_duration_min", v)} min={5} max={360} step={5} unit="min" />
            </Field>
          </div>
          <p className="field-help">{t("heat.minHelp")}</p>
          <Field label={t("heat.kind")}>
            <Segment<HeatKind>
              value={(d.str("kind", kind) as HeatKind)}
              onChange={(v) => d.set("kind", v)}
              options={(["heat_pump", "climate", "water", "heating"] as HeatKind[]).map((k) => ({
                value: k, label: t(HEAT_LABEL[k]), icon: HEAT_ICON[k],
              }))}
            />
          </Field>
          {/* Klimaanlage regelt immer selbst (Raumtemperatur, Heizen und Kühlen) */}
          {d.str("kind", kind) !== "climate" && (
            <ToggleRow label={t("heat.selfReg")} help={t("heat.selfRegHelp")}
                       on={d.draft.self_regulating !== undefined ? d.draft.self_regulating === true : dev.self_regulating === true}
                       onChange={(v) => d.set("self_regulating", v)} />
          )}
          <div className="form-actions">
            {d.dirty && <Button variant="ghost" onClick={d.reset}>{t("set.discard")}</Button>}
            <ActionButton variant="primary" disabled={!d.dirty} onClick={() => saveSettings(dev.id, {
              min_on_s: d.draft.min_on_s, min_off_s: d.draft.min_off_s, kind: d.draft.kind,
              self_regulating: d.draft.self_regulating, boost_duration_min: d.draft.boost_duration_min,
              // Freigabe von Hand endet nach Zeit – Temperatur gilt nicht
              boost_end_mode: "time",
            })}>
              {t("set.save")}
            </ActionButton>
          </div>
        </Disclosure>
        <WaterAdvanced saved={saved} globalLimit={snapshot?.price_limit_ct ?? null}
                       onSave={(x) => saveSettings(dev.id, x)} />
      </Card>
    </>
  );
}

/* ------------------------------------------------------------ Übersicht aller Geräte */

/** Eine Kachel je Wärmegerät: Art, Leistung, Temperatur, Satz, Platz in der
 *  Reihenfolge – ein Tipp springt zu den Details weiter unten. */
function HeatOverview({ heaters, priority }: { heaters: SnapshotDevice[]; priority: number[] }) {
  const { t } = useI18n();
  return (
    <section className="heat-overview" aria-label={t("heat.all")}>
      {heaters.map((h) => {
        const kind = heatKind(h);
        const temp = devTemp(h);
        const s = heatSentence(h, t);
        const pos = priority.indexOf(h.id);
        return (
          <a key={h.id} href={`#dev-${h.id}`} className={`card heat-tile tone-${HEAT_TONE[kind]} ${devPower(h) > 50 ? "on" : ""}`}
             onClick={(e) => { e.preventDefault(); scrollToDevice(h.id); }}>
            <span className={`quick-icon tone-${HEAT_TONE[kind]}`}><Icon name={HEAT_ICON[kind]} size={22} /></span>
            <span className="heat-tile-main">
              <strong>{h.name}</strong>
              <small>{t(HEAT_LABEL[kind])}{pos >= 0 ? ` · ${t("heat.priority", { n: pos + 1 })}` : ""}</small>
              <span className={`heat-tile-status tone-${s.tone}`}>{s.text}</span>
            </span>
            <span className="heat-tile-values num">
              <b>{fmtW(devPower(h))}</b>
              {temp != null && <small>{fmtTemp(temp)}</small>}
            </span>
          </a>
        );
      })}
    </section>
  );
}

function scrollToDevice(id: number) {
  document.getElementById(`dev-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** Sprungziel aus Energiefluss und Übersicht (#dev-12) nach dem Laden anfahren. */
export function useDeviceAnchor(ready: boolean) {
  const { hash } = useLocation();
  useEffect(() => {
    const m = /^#dev-(\d+)$/.exec(hash);
    if (!ready || !m) return;
    const id = Number(m[1]);
    const timer = window.setTimeout(() => scrollToDevice(id), 60);
    return () => window.clearTimeout(timer);
  }, [hash, ready]);
}

export function Water() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const snapshot = useLive((s) => s.snapshot);
  const heaters = snapshot?.devices.filter((d) => d.category === "water_heater" && d.enabled) ?? [];
  useDeviceAnchor(heaters.length > 0);
  if (!snapshot) return <div className="page"><div className="skeleton" style={{ height: 300 }} /></div>;
  const priority = snapshot.priority ?? [];
  const sorted = [...heaters].sort((a, b) => {
    const pa = priority.indexOf(a.id), pb = priority.indexOf(b.id);
    return (pa < 0 ? 999 : pa) - (pb < 0 ? 999 : pb);
  });
  const multi = sorted.length > 1;
  const single = sorted.length === 1 ? sorted[0] : null;
  const title = single && !single.self_regulating ? t("heat.title") : t("heat.pageTitle");
  return (
    <div className="page">
      <PageHead title={title} sub={multi ? t("heat.pageSub") : t("heat.sub")} />
      {!sorted.length ? (
        <Card>
          <Empty icon={<Icon name="water" size={32} />} title={t("heat.none")}
                 action={<Button variant="primary" onClick={() => navigate("/einstellungen?tab=devices")}>{t("car.addDevice")}</Button>}>
            {t("heat.noneSub")}
          </Empty>
        </Card>
      ) : (
        <>
          {multi && <HeatOverview heaters={sorted} priority={priority} />}
          {sorted.map((h) => (
            <section key={h.id} id={`dev-${h.id}`} className="device-section">
              {multi && (
                <h2 className="device-section-head">
                  <span className={`quick-icon tone-${HEAT_TONE[heatKind(h)]}`}><Icon name={HEAT_ICON[heatKind(h)]} size={20} /></span>
                  {h.name}
                  <small>{t(HEAT_LABEL[heatKind(h)])}</small>
                </h2>
              )}
              {h.self_regulating ? <HeatPumpDetail dev={h} /> : <WaterDetail dev={h} />}
            </section>
          ))}
        </>
      )}
    </div>
  );
}
