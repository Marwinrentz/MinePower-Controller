/** Auto – Ladepunkt im Detail.
 *
 *  Oben die Garage als Zustandsbild und darunter die Zahlen, auf die es beim
 *  Laden ankommt. Dann EINE Auswahl für die Betriebsart: Früher gab es eine
 *  Modus-Auswahl und daneben drei Knöpfe 'Jetzt laden / Stopp / Automatik",
 *  die dasselbe Thema anders regelten. Jetzt sind 'Sofort voll" und 'Aus"
 *  einfach zwei weitere Betriebsarten – was gewählt ist, gilt.
 */
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { DevicePicker } from "../components/DevicePicker";
import { Garage, type GarageState } from "../components/Garage";
import { Icon } from "../components/icons";
import {
  ActionButton, Badge, Button, Callout, Card, CardHead, Disclosure, Empty, Field, NumberInput, PageHead,
  Segment, Slider, Stat, ToggleRow,
} from "../components/ui";
import { useI18n } from "../i18n";
import { confirmTouch } from "../components/Confirm";
import { get } from "../lib/api";
import { carSentence, carState, shortError, startIn } from "../lib/explain";
import { fmtClock, fmtCt, fmtKwh, fmtPct, fmtW, fmtNum } from "../lib/format";
import type { SnapshotDevice } from "../lib/types";
import { useDeviceCommand } from "../lib/useCommand";
import { useDevices } from "../lib/useDevices";
import { useDraft } from "../lib/useDraft";
import { useLive } from "../store/live";
import { toast } from "../store/toast";

type ModeKey = "off" | "pv_only" | "min_pv" | "pv_price" | "target" | "fast";
const MODES: ModeKey[] = ["pv_only", "min_pv", "pv_price", "target", "fast", "off"];
const MODE_ICON = { off: "stop", pv_only: "sun", min_pv: "plug", pv_price: "price", target: "clock", fast: "bolt" } as const;

type Session = { id: number; device_id: number; started_at: string; energy_kwh: number; solar_kwh: number };

/* ------------------------------------------------------------ Ladevorgänge */

function Cycles({ deviceId }: { deviceId: number }) {
  const { t } = useI18n();
  const [rows, setRows] = useState<Session[] | null>(null);
  useEffect(() => {
    get<Session[]>("/api/sessions?limit=40")
      .then((r) => setRows(r.filter((x) => x.device_id === deviceId)))
      .catch(() => setRows([]));
  }, [deviceId]);
  const max = useMemo(() => Math.max(1, ...(rows ?? []).map((r) => r.energy_kwh)), [rows]);
  if (!rows || rows.length === 0) return null;
  return (
    <Card>
      <CardHead title={t("car.cycles")} sub={t("car.cyclesHelp")} />
      <ul className="cycles">
        {rows.map((r) => {
          const solar = r.energy_kwh > 0 ? r.solar_kwh / r.energy_kwh : 0;
          const d = new Date(r.started_at);
          return (
            <li key={r.id} className="cycle">
              <span className="cycle-when">
                <b>{d.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" })}</b>
                <span>{d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
              </span>
              <span className="cycle-track">
                <span className="cycle-bar" style={{ width: `${(r.energy_kwh / max) * 100}%` }}>
                  <span className="cycle-solar" style={{ width: `${solar * 100}%` }} />
                </span>
              </span>
              <span className="cycle-nums">
                <b className="num">{fmtKwh(r.energy_kwh)}</b>
                <span className="num">{Math.round(solar * 100)} % ☀</span>
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/* ------------------------------------------------------------ Erweitert */

function CarAdvanced({ dev, saved, onSave, globalLimit }: {
  dev: SnapshotDevice; saved: Record<string, unknown> | undefined;
  onSave: (s: Record<string, unknown>) => Promise<boolean>; globalLimit: number | null;
}) {
  const { t } = useI18n();
  const d = useDraft(saved);
  const detected = dev.phases_detected ?? null;
  const setPhases = d.str("phases_mode", "fixed1");
  const setN = setPhases === "fixed3" ? 3 : setPhases === "fixed1" ? 1 : null;
  const canSwitch = (dev.capabilities ?? []).includes("phase_switch");
  const minA = d.num("min_current", 6);
  const minPower = (detected ?? setN ?? 1) * minA * 230;

  return (
    <Disclosure title={t("car.advanced")} summary={t("car.advancedSummary")}>
      <div className="form-grid">
        <Field label={t("car.minCurrent")} help={t("car.minCurrentHelp")}>
          <NumberInput value={d.num("min_current", 6)} onChange={(v) => d.set("min_current", v)} min={1} max={32} unit="A" />
        </Field>
        <Field label={t("car.maxCurrent")} help={t("car.maxCurrentHelp")}>
          <NumberInput value={d.num("max_current", 16)} onChange={(v) => d.set("max_current", v)} min={1} max={63} unit="A" />
        </Field>
      </div>

      <Field label={t("car.phasesSet")} help={t("car.phasesHelp")}>
        <Segment
          size="lg"
          value={setPhases}
          onChange={(v) => d.set("phases_mode", v)}
          options={[
            { value: "fixed1", label: t("car.phase1") },
            { value: "fixed3", label: t("car.phase3") },
            ...(canSwitch ? [{ value: "auto", label: t("car.phaseAuto") }] : []),
          ]}
        />
      </Field>
      {detected != null && setN != null && detected !== setN ? (
        <Callout tone="warn" action={
          <Button size="sm" onClick={() => d.set("phases_mode", detected === 3 ? "fixed3" : "fixed1")}>
            {t(detected === 3 ? "car.phase3" : "car.phase1")}
          </Button>
        }>
          {t("car.phasesMismatch", { set: setN, n: detected })}
        </Callout>
      ) : detected != null ? (
        <p className="field-help">{t("car.phasesDetected", { n: detected, min: fmtW(detected * minA * 230) })}</p>
      ) : null}

      <div className="form-grid">
        <Field label={t("car.startThreshold")} help={t("car.startThresholdHelp")}>
          <NumberInput value={d.num("start_threshold_w", 1400)} onChange={(v) => d.set("start_threshold_w", v)}
                       min={0} step={100} unit="W" />
          {d.num("start_threshold_w", 1400) < minPower && (
            <small>{`= ${fmtW(minPower)}`}</small>
          )}
        </Field>
        <Field label={t("car.startDelay")} help={t("car.startDelayHelp")}>
          <NumberInput value={d.num("start_delay_s", 60)} onChange={(v) => d.set("start_delay_s", v)} min={0} step={10} unit="s" />
        </Field>
        <Field label={t("car.stopDelay")} help={t("car.stopDelayHelp")}>
          <NumberInput value={d.num("stop_delay_s", 180)} onChange={(v) => d.set("stop_delay_s", v)} min={0} step={10} unit="s" />
        </Field>
      </div>

      <div className="form-grid">
        <Field label={t("car.targetSoc")} help={t("car.targetHelp")}>
          <NumberInput value={d.num("target_soc", 80)} onChange={(v) => d.set("target_soc", v)} min={10} max={100} unit="%" />
        </Field>
        <Field label={t("car.targetTime")}>
          <input className="input" type="time" value={d.str("target_time", "07:00")}
                 onChange={(e) => d.set("target_time", e.target.value)} />
        </Field>
      </div>

      <p className="field-help">
        {t("car.priceOne", { limit: globalLimit != null ? fmtCt(globalLimit) : "–" })}
      </p>

      <ToggleRow label={t("car.gridMetered")} help={t("car.gridMeteredHelp")}
                 on={d.bool("grid_metered", true)} onChange={(v) => d.set("grid_metered", v)} />

      <div className="form-actions">
        {d.dirty && <Button variant="ghost" onClick={d.reset}>{t("set.discard")}</Button>}
        <ActionButton variant="primary" disabled={!d.dirty} onClick={() => onSave(d.draft)}>{t("set.save")}</ActionButton>
      </div>
    </Disclosure>
  );
}

/* ------------------------------------------------------------ Detail */

function CarDetail({ dev }: { dev: SnapshotDevice }) {
  const { t } = useI18n();
  const { devices, saveSettings } = useDevices();
  const snapshot = useLive((s) => s.snapshot);
  const caps = dev.capabilities ?? [];
  const state = carState(dev);
  const power = Number(dev.data?.power ?? 0);
  const soc = typeof dev.data?.soc === "number" ? (dev.data.soc as number) : null;
  const current = typeof dev.data?.current_set === "number" ? (dev.data.current_set as number) : null;
  const phases = typeof dev.data?.phases_active === "number" ? (dev.data.phases_active as number) : dev.phases_detected ?? null;
  const session = dev.session;
  const sessionKwh = typeof dev.data?.energy_session_kwh === "number"
    ? (dev.data.energy_session_kwh as number) : session?.energy_kwh ?? null;
  const solarShare = session && session.energy_kwh > 0.01 ? session.solar_kwh / session.energy_kwh : null;

  const confirmed = useMemo(() => ({
    mode: dev.mode ?? "pv_only",
    override: dev.override ?? null,
    limit: dev.charge_limit_soc == null ? null : Math.round(Number(dev.charge_limit_soc)),
  }), [dev.mode, dev.override, dev.charge_limit_soc]);
  const cmd = useDeviceCommand(dev.id, confirmed);
  const mode = cmd.shown<string>("mode", confirmed.mode);
  const override = cmd.shown<string | null>("override", confirmed.override);
  const limit = cmd.shown<number | null>("limit", confirmed.limit);
  const value: ModeKey = override === "stop" ? "off" : override === "fast" ? "fast" : (mode as ModeKey);
  const pendingMode = cmd.pending("mode") || cmd.pending("override") ? value : null;

  const choose = async (v: ModeKey) => {
    if (v === "off") return void cmd.send("stop", undefined, { override: "stop" });
    if (v === "fast") {
      if (!(await confirmTouch({ title: t("confirm.fastTitle"), icon: "bolt", confirmLabel: t("modes.car.fast"),
                                 text: t("confirm.fastText") }))) return;
      return void cmd.send("fast", undefined, { override: "fast" });
    }
    if (override) await cmd.send("auto", undefined, { override: null });
    if (mode !== v) await cmd.send("set_mode", v, { mode: v });
  };

  // Angesteckt (auch schlafend) = Auto in der Garage; unterwegs = leer;
  // Proxy offline/unbekannt = Garage ohne Aussage (kein Hineinfahren-Theater).
  const gState: GarageState = state === "charging" ? "charging"
    : state === "asleep" ? "asleep"
    : state === "waiting" || state === "complete" ? "waiting"
    : state === "away" || state === "unplugged" ? "away" : "unknown";

  const err = dev.last_error ?? "";
  const addr = err.match(/https?:\/\/([\w.-]+(?::\d+)?)/)?.[1] ?? "";
  const ov = dev.override_state;
  const ovUntil = ov?.until ? fmtClock(ov.until) : null;
  const hint = state === "proxy" ? t("car.hintProxy", { addr: addr || "–" })
    : state === "offline" ? t("car.hintOffline", { error: shortError(err) || "–" })
    : state === "asleep" ? t("car.hintAsleep")
    : state === "away" ? t("car.hintAway")
    : state === "unknown" ? t("car.hintUnknown")
    : state === "unplugged" ? t("car.hintUnplugged")
    : state === "complete" ? t("car.hintComplete")
    : state === "waiting" && override === "stop" ? t("car.hintStopped")
    : state === "waiting" && /nicht ladebereit|keine Leistung/.test(String(dev.decision?.reason ?? "")) ? t("car.hintNotReady")
    : state === "waiting" && startIn(String(dev.decision?.reason ?? "")) != null
      ? t("car.hintStarting", { sec: startIn(String(dev.decision?.reason ?? "")) ?? 0 })
    : state === "waiting" && /Vorrang/.test(String(dev.decision?.reason ?? "")) ? t("explain.carBatteryFirst")
    : state === "waiting" ? t("car.hintWaiting", { min: fmtW(dev.start_threshold_w ?? dev.min_power_w ?? 1400) })
    : carSentence(dev, t).text;

  const helpText = t(`modes.carHelp.${value}`);
  const saved = devices?.find((d) => d.id === dev.id)?.settings;

  const wake = async () => {
    const ok = await cmd.send("wake");
    if (ok) toast.ok(t("car.woken"));
  };

  return (
    <>
      <Card className={`car-hero state-${state}`}>
        <div className="car-hero-art">
          <Garage state={gState} soc={soc} label={t("car.garage")} />
        </div>
        <div className="car-hero-info">
          <div className="row wrap tight">
            <Badge tone={state === "charging" ? "ok" : state === "offline" || state === "proxy" ? "err" : state === "asleep" ? "warn" : "info"}>
              {t(`car.state.${state}`)}
            </Badge>
            {ov && (
              <Badge tone="accent" title={t("car.overrideEnds")}>
                {t(ov.kind === "stop" ? "car.overrideStop" : "car.overrideFast", { until: ovUntil ?? "–" })}
              </Badge>
            )}
            {dev.uncontrolled && <Badge tone="warn">{t("home.commandFailed")}</Badge>}
          </div>
          <h2 className="car-name">{dev.name}</h2>
          <p className="car-hint">{hint}</p>
          {dev.wake_note && state === "asleep" && <p className="field-help">{dev.wake_note}</p>}
          <div className="stats">
            <Stat label={t("car.power")} value={fmtW(power)} tone={state === "charging" ? "car" : undefined} />
            <Stat label={t("car.soc")} value={soc != null ? fmtPct(soc) : "–"} />
            <Stat label={t("car.current")} value={current != null && state === "charging" ? `${fmtNum(current)} A` : "–"} />
            <Stat label={t("car.phases")} value={phases != null ? String(phases) : "–"} />
            <Stat label={t("car.charged")} value={sessionKwh != null ? fmtKwh(sessionKwh) : "–"} sub={t("car.since")} />
            <Stat label={t("car.solar")} value={solarShare != null ? fmtPct(solarShare * 100) : "–"} tone={solarShare != null ? "pv" : undefined} />
          </div>
        </div>
      </Card>

      <Card>
        <CardHead title={t("car.mode")} />
        <Segment<ModeKey>
          size="lg"
          ariaLabel={t("car.mode")}
          value={value}
          onChange={(v) => void choose(v)}
          pending={pendingMode}
          options={MODES.map((m) => ({ value: m, label: t(`modes.car.${m}`), icon: MODE_ICON[m] }))}
        />
        <p className="mode-help" aria-live="polite">{helpText}</p>
        {caps.includes("wake") && (
          <div className="row wrap">
            <ActionButton variant="ghost" onClick={wake} title={t("car.wakeHelp")}>
              <Icon name="power" size={16} /> {t("car.wake")}
            </ActionButton>
          </div>
        )}
      </Card>

      {caps.includes("charge_limit") && (
        <Card>
          <Slider
            label={t("car.limit")}
            help={t("car.limitHelp")}
            value={limit ?? 80}
            min={50}
            max={100}
            step={1}
            tone="car"
            format={(v) => `${v} %`}
            marks={[{ value: 50, label: "50 %" }, { value: 80, label: "80 %" }, { value: 100, label: "100 %" }]}
            onCommit={(v) => void cmd.send("set_charge_limit", v, { limit: v })}
          />
        </Card>
      )}

      <Card>
        <CarAdvanced dev={dev} saved={saved} onSave={(s) => saveSettings(dev.id, s)}
                     globalLimit={snapshot?.price_limit_ct ?? null} />
      </Card>

      <Cycles deviceId={dev.id} />
    </>
  );
}

export function Car() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const snapshot = useLive((s) => s.snapshot);
  const [picked, setPicked] = useState<number | null>(null);

  if (!snapshot) return <div className="page"><div className="skeleton" style={{ height: 300 }} /></div>;
  const cars = snapshot.devices.filter((d) => d.category === "wallbox" && d.enabled);
  const car = cars.find((c) => c.id === picked) ?? cars[0];

  return (
    <div className="page">
      <PageHead title={t("car.title")} sub={t("car.sub")} />
      {!car ? (
        <Card>
          <Empty icon={<Icon name="car" size={32} />} title={t("car.none")}
                 action={<Button variant="primary" onClick={() => navigate("/einstellungen?tab=devices")}>{t("car.addDevice")}</Button>}>
            {t("car.noneSub")}
          </Empty>
        </Card>
      ) : (
        <>
          <DevicePicker devices={cars} active={car.id} onPick={setPicked} />
          <CarDetail key={car.id} dev={car} />
        </>
      )}
    </div>
  );
}
