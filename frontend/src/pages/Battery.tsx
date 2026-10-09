/** Batterie – Zustand, Grenzen und Steuerung von Hand.
 *
 *  Die Grafik zeigt den Ladestand zusammen mit den Grenzen, die sein
 *  Verhalten bestimmen: Reserve, 'Vorrang bis" und die Untergrenze im
 *  Wechselrichter. Darunter große Knöpfe für Laden/Entladen/Automatik. Jeder
 *  Befehl von Hand endet von selbst (Dauer oder Ziel) – es gibt kein
 *  vergessenes Zwangsladen.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { BatteryGauge, type GaugeMark } from "../components/BatteryGauge";
import { SocChart } from "../components/charts";
import { Icon } from "../components/icons";
import {
  Badge, Button, Callout, Card, CardHead, Empty, PageHead, Segment, Slider, Stat, useCountdown,
} from "../components/ui";
import { useI18n } from "../i18n";
import { confirmTouch } from "../components/Confirm";
import { post } from "../lib/api";
import { fmtDuration, fmtPct, fmtW } from "../lib/format";
import type { BatteryMode, SnapshotDevice } from "../lib/types";
import { useLive } from "../store/live";
import { useSettings } from "../store/settings";
import { toast } from "../store/toast";

type Action = "auto" | "no_discharge" | "hold" | "charge" | "discharge";
const ACTIONS: { key: Action; icon: "auto" | "shield" | "lock" | "bolt" | "arrowDown" }[] = [
  { key: "auto", icon: "auto" },
  { key: "no_discharge", icon: "shield" },
  { key: "hold", icon: "lock" },
  { key: "charge", icon: "bolt" },
  { key: "discharge", icon: "arrowDown" },
];
const DURATIONS = [15, 30, 60, 120, 240];

function manualAction(m: { intent?: string | null; mode: string } | null): Action {
  if (!m) return "auto";
  if (m.intent && ["no_discharge", "hold", "charge", "discharge"].includes(m.intent)) return m.intent as Action;
  return m.mode === "force_discharge" ? "discharge" : m.mode === "hold" ? "hold" : "charge";
}

function ManualControl({ dev, reserve }: { dev: SnapshotDevice; reserve: number }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const snapshot = useLive((s) => s.snapshot);
  const manualAll = snapshot?.battery_manual ?? null;
  const manual = manualAll && (manualAll.device_id == null || manualAll.device_id === dev.id) ? manualAll : null;
  const defaults = useSettings((s) => s.values.regulation ?? {});
  const maxPower = Math.max(500, Number(dev.max_power_w ?? 5000));
  const maxMinutes = Number(defaults.battery_manual_max_min ?? 240) || 240;
  const controllable = dev.control_enabled !== false && dev.control_capable !== false;

  const [pick, setPick] = useState<Action>("no_discharge");
  const [power, setPower] = useState(() => Math.min(maxPower, Number(defaults.battery_manual_power_w ?? 3000) || 3000));
  const [minutes, setMinutes] = useState(60);
  const [target, setTarget] = useState(100);
  const [want, setWant] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const remaining = useCountdown(manual?.remaining_s);

  const confirmed = manualAction(manual);
  useEffect(() => { if (want !== null && want === confirmed) setWant(null); }, [want, confirmed]);
  const needsPower = pick === "charge" || pick === "discharge";

  const send = async (action: Action) => {
    if (busy) return; // kein Doppelklick
    if (action !== "auto" && !(await confirmTouch({
      title: `${t(`bat.act.${action}`)}?`, confirmLabel: t(`bat.act.${action}`),
      icon: ACTIONS.find((x) => x.key === action)?.icon ?? "battery",
      text: t("confirm.batteryText", { time: fmtDuration(minutes * 60) }),
    }))) return;
    setBusy(true);
    setWant(action);
    try {
      const body = action === "auto" ? { action }
        : needsPowerFor(action)
          ? { action, device_id: dev.id, power_w: power, minutes,
              target_soc: action === "charge" ? target : Math.max(reserve, target) }
          : { action, device_id: dev.id, minutes };
      await post("/api/battery/manual", body);
      toast.ok(action === "auto" ? t("bat.stopped") : t("bat.started"));
    } catch (e) {
      setWant(null);
      toast.err(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!controllable) {
    return (
      <Callout tone="info" title={t("bat.manualTitle")}
               action={dev.control_capable !== false
                 ? <Button size="sm" onClick={() => navigate("/einstellungen?tab=devices")}>{t("bat.enable")}</Button>
                 : undefined}>
        {dev.control_capable === false ? t("bat.notCapable") : t("bat.notEnabled")}
      </Callout>
    );
  }

  if (manual) {
    const a = manualAction(manual);
    return (
      <div className="manual-running" aria-live="polite">
        <span className="manual-running-title"><Icon name={ACTIONS.find((x) => x.key === a)?.icon ?? "auto"} size={18} /> {t(`bat.act.${a}`)}</span>
        <span className="num">{t("bat.runningLeft", { time: fmtDuration(remaining) })}
          {manual.target_soc != null && (a === "charge" || a === "discharge") ? ` · ${t("bat.until", { soc: Math.round(manual.target_soc) })}` : ""}</span>
        <Button size="lg" block loading={busy && want === "auto"} onClick={() => void send("auto")}>
          <Icon name="auto" size={18} /> {t("bat.backToAuto")}
        </Button>
      </div>
    );
  }

  return (
    <>
      <div className="manual-modes" role="radiogroup" aria-label={t("bat.manualTitle")}>
        {ACTIONS.filter((x) => x.key !== "auto").map((x) => (
          <button key={x.key} type="button" role="radio" aria-checked={pick === x.key}
                  className={`manual-mode ${pick === x.key ? "active" : ""}`} onClick={() => setPick(x.key)}>
            <Icon name={x.icon} size={20} />
            <span className="manual-mode-label">{t(`bat.act.${x.key}`)}</span>
            <span className="manual-mode-help">{t(`bat.actHelp.${x.key}`)}</span>
          </button>
        ))}
      </div>
      <div className="manual-params">
        <div className="field">
          <label>{t("bat.duration")}</label>
          <Segment size="lg" value={String(minutes)} onChange={(v) => setMinutes(Number(v))}
                   options={DURATIONS.filter((m) => m <= maxMinutes).map((m) => ({ value: String(m), label: fmtDuration(m * 60) }))} />
        </div>
        {needsPower && (
          <div className="form-grid">
            <Slider label={t("bat.power")} value={power} min={500} max={maxPower} step={100} tone="battery"
                    format={(v) => fmtW(v)} onChange={setPower} onCommit={setPower} />
            <Slider label={pick === "charge" ? t("bat.chargeTo") : t("bat.dischargeTo")}
                    value={pick === "charge" ? target : Math.max(reserve, Math.min(target, 95))}
                    min={pick === "charge" ? 20 : reserve} max={pick === "charge" ? 100 : 95} step={5} tone="battery"
                    format={(v) => `${v} %`} onChange={setTarget} onCommit={setTarget} />
          </div>
        )}
        <Button size="lg" variant="primary" block loading={busy && want === pick} disabled={busy}
                onClick={() => void send(pick)}>
          <Icon name={ACTIONS.find((x) => x.key === pick)?.icon ?? "auto"} size={18} />
          {t("bat.start", { what: t(`bat.act.${pick}`), time: fmtDuration(minutes * 60) })}
        </Button>
      </div>
      {snapshot?.battery_manual_last && (
        <p className="field-help">{t("bat.lastEnded", { reason: snapshot.battery_manual_last.reason })}</p>
      )}
    </>
  );
}

function needsPowerFor(a: Action): boolean {
  return a === "charge" || a === "discharge";
}

export function Battery() {
  const { t } = useI18n();
  const location = useLocation();
  const snapshot = useLive((s) => s.snapshot);
  const loadSettings = useSettings((s) => s.load);
  const loaded = useSettings((s) => s.loaded);
  const reg = useSettings((s) => s.values.regulation ?? {});
  useEffect(() => { if (!loaded) void loadSettings(); }, [loaded, loadSettings]);
  useEffect(() => {
    if (location.hash === "#manuell") document.getElementById("manuell")?.scrollIntoView({ behavior: "smooth" });
  }, [location.hash]);

  const dev = useMemo(() => snapshot?.devices.find((d) => d.category === "battery" && d.enabled), [snapshot]);
  if (!snapshot) return <div className="page"><div className="skeleton" style={{ height: 300 }} /></div>;

  const bat = snapshot.battery;
  if (!bat && !dev) {
    return (
      <div className="page">
        <PageHead title={t("bat.title")} sub={t("bat.sub")} />
        <Card><Empty icon={<Icon name="battery" size={32} />} title={t("bat.none")}>{t("bat.noneSub")}</Empty></Card>
      </div>
    );
  }

  const soc = bat?.soc ?? (typeof dev?.data?.soc === "number" ? (dev.data.soc as number) : null);
  const p = bat?.power ?? Number(dev?.data?.power ?? 0);
  const reserve = Math.round(Number(snapshot.battery_reserve_soc ?? 20));
  const priority = Number(snapshot.battery_priority_soc ?? reg.battery_priority_soc ?? 100);
  const plan = snapshot.battery_plan;
  const invMin = typeof dev?.data?.min_soc === "number" ? (dev.data.min_soc as number) : null;
  const invMax = typeof dev?.data?.max_soc === "number" ? (dev.data.max_soc as number) : null;
  const readback = dev?.data?.mode_known ? (String(dev.data.mode) as BatteryMode) : null;
  const differs = !!readback && !!dev?.target_mode && readback !== dev.target_mode;

  const marks: GaugeMark[] = [{ soc: reserve, label: `${t("bat.reserve")} ${reserve} %`, kind: "reserve" }];
  if (priority > 0 && priority < 100) {
    marks.push({ soc: priority, label: `${t("bat.priorityUntil")} ${priority} %`, kind: "priority" });
  }

  const status = snapshot.battery_grid_charging ? t("bat.sGrid")
    : p > 30 ? t("bat.sCharging", { power: fmtW(p) })
    : p < -30 ? t("bat.sDischarging", { power: fmtW(-p) }) : t("bat.sIdle");
  const planText = plan && plan.intent !== "auto" ? plan.reason : null;

  return (
    <div className="page">
      <PageHead title={t("bat.title")} sub={t("bat.sub")} />

      <Card className="bat-hero">
        <div className="bat-art">
          <BatteryGauge soc={soc} power={p} gridCharging={snapshot.battery_grid_charging} marks={marks} label={t("bat.title")} />
        </div>
        <div className="bat-main">
          <div className="row wrap tight">
            <Badge tone={plan && plan.intent !== "auto" ? "accent" : undefined}>{plan?.label ?? t("bat.act.auto")}</Badge>
            {snapshot.battery_manual && <Badge tone="info">{t("bat.manualBadge")}</Badge>}
          </div>
          <p className="bat-status">{status}</p>
          {planText && <p className="bat-plan">{planText}</p>}
          <p className="field-help">{snapshot.battery_gate_reason}</p>
          <div className="stats">
            <Stat label={t("car.soc")} value={soc != null ? fmtPct(soc) : "–"} tone="battery" />
            <Stat label={t("car.power")} value={fmtW(Math.abs(p))}
                  sub={p > 30 ? t("flow.charging") : p < -30 ? t("flow.discharging") : t("flow.idle")} />
            {readback && (
              <Stat label={t("bat.inverterMode")} value={t(`bat.mode.${readback}`)}
                    sub={differs ? (dev?.mode_settling ? t("bat.settling") : t("bat.mismatch")) : undefined} />
            )}
            {invMin != null && invMax != null && (
              <Stat label={t("bat.inverterLimits")} value={`${Math.round(invMin)}–${Math.round(invMax)} %`}
                    sub={dev?.inverter_reserve != null && dev.inverter_reserve + 0.5 < reserve
                      ? t("bat.reserveSplit", { inv: Math.round(dev.inverter_reserve), reserve }) : undefined} />
            )}
          </div>
        </div>
      </Card>

      <Card id="manuell">
        <CardHead title={t("bat.manualTitle")} sub={t("bat.manualHelp")} />
        {dev ? <ManualControl dev={dev} reserve={reserve} /> : <Callout tone="info">{t("bat.monitorOnly")}</Callout>}
      </Card>

      <Card>
        <CardHead title={t("bat.day")}>
          <Link className="btn ghost btn-sm" to="/einstellungen?tab=battery"><Icon name="settings" size={16} /> {t("bat.toSettings")}</Link>
        </CardHead>
        <SocChart range="24h" />
      </Card>
    </div>
  );
}
