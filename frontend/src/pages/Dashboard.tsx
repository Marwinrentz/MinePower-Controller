/** Übersicht – der Zustand der Anlage auf einen Blick.
 *
 *  Reihenfolge nach Wichtigkeit:
 *   1. Was nicht stimmt (Pause, Sicherheitsstopp, Geräte offline, Hinweise).
 *   2. Was gerade passiert – als Satz – und warum (Liste in Alltagssprache).
 *   3. Der Energiefluss.
 *   4. Schnellzugriff: Warmwasser-Boost, Auto, Batterie – die Handgriffe,
 *      für die man die App überhaupt öffnet.
 *   5. Kennzahlen und Verlauf.
 *
 *  Eine Seite für alle Bildschirme: am Handy untereinander, breit in zwei
 *  Spalten (Fluss links, Schnellzugriff rechts).
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PowerHistoryChart } from "../components/charts";
import { confirmTouch } from "../components/Confirm";
import { Icon } from "../components/icons";
import { PowerFlow } from "../components/PowerFlow";
import {
  ActionButton, Banner, Button, Card, CardHead, Disclosure, Empty, Segment, useCountdown, useIsPhone,
} from "../components/ui";
import { useI18n } from "../i18n";
import { get, post } from "../lib/api";
import { carSentence, carState, explain, shortError, waterSentence } from "../lib/explain";
import { fmtClock, fmtCt, fmtDuration, fmtKwh, fmtPct, fmtTemp, fmtW } from "../lib/format";
import type { Snapshot, SnapshotDevice, Statistics } from "../lib/types";
import { useDeviceCommand } from "../lib/useCommand";
import { useLive } from "../store/live";
import { toast } from "../store/toast";

const RANGES = ["6h", "24h", "7d"] as const;
type Forecast = { today_kwh?: number | null; active?: boolean };

/* ------------------------------------------------------------ Warmwasser */

export function boostPlan(dev: SnapshotDevice, t: (k: string, v?: Record<string, string | number>) => string): string {
  const endMode = dev.boost_end_mode ?? "both";
  const time = fmtDuration((dev.boost_duration_min ?? 60) * 60);
  const temp = fmtTemp(dev.boost_temp_c);
  if (endMode === "time") return t("heat.boostPlanTime", { time });
  if (endMode === "temp") return t("heat.boostPlanTemp", { temp });
  return t("heat.boostPlan", { time, temp });
}

/** Boost wäre wirkungslos: Das Wasser ist schon wärmer als das Boost-Ziel und
 *  der Boost endet nach Temperatur (bei 'nur Zeit" heizt er trotzdem). */
export function boostTooHot(dev: SnapshotDevice): boolean {
  const temp = typeof dev.data?.temperature_c === "number" ? (dev.data.temperature_c as number) : null;
  return !dev.boost && temp != null && dev.boost_temp_c != null && temp >= dev.boost_temp_c
    && (dev.boost_end_mode ?? "both") !== "time";
}

/** Am Touchscreen kurz nachfragen (mit Maus sofort): Ein Tipp am Rand
 *  beim Scrollen soll keinen Boost und keine Volllast auslösen. */
function confirmBoost(dev: SnapshotDevice, t: (k: string, v?: Record<string, string | number>) => string) {
  return confirmTouch({ title: t("confirm.boostTitle"), icon: "boost", confirmLabel: t("heat.boostStart"),
                        text: t("confirm.boostText", { plan: boostPlan(dev, t) }) });
}

function confirmFast(t: (k: string) => string) {
  return confirmTouch({ title: t("confirm.fastTitle"), icon: "bolt", confirmLabel: t("modes.car.fast"),
                        text: t("confirm.fastText") });
}

function QuickWater({ dev, named = false }: { dev: SnapshotDevice; named?: boolean }) {
  const { t } = useI18n();
  const confirmed = useMemo(() => ({ boost: dev.boost === true }), [dev.boost]);
  const cmd = useDeviceCommand(dev.id, confirmed);
  const boost = cmd.shown("boost", confirmed.boost);
  const remaining = useCountdown(dev.boost ? dev.boost_state?.remaining_s : null);
  const temp = typeof dev.data?.temperature_c === "number" ? (dev.data.temperature_c as number) : null;
  const power = Number(dev.data?.power ?? 0);
  const s = waterSentence(dev, t);
  const endMode = dev.boost_end_mode ?? "both";
  const busy = cmd.pending("boost");

  const toggle = async () => {
    if (!boost && !(await confirmBoost(dev, t))) return;
    void cmd.send(boost ? "boost_off" : "boost", undefined, { boost: !boost });
  };

  return (
    <Card className="quick quick-water">
      <div className="quick-head">
        <span className="quick-icon tone-water"><Icon name="water" size={22} /></span>
        <div className="quick-title">
          <h2>{named ? dev.name : t("heat.title")}</h2>
          <span className="quick-meta num">{fmtTemp(temp)} · {fmtW(power)}</span>
        </div>
        <Link to="/warmwasser" className="btn ghost btn-sm quick-more">{t("home.details")} <Icon name="chevron" size={16} /></Link>
      </div>
      <p className={`quick-status tone-${s.tone}`}>{s.text}</p>
      {boost ? (
        <div className="boost-running" aria-live="polite">
          <div className="boost-running-text">
            <strong><Icon name="boost" size={18} /> {t("heat.boostRunning")}</strong>
            <span className="num">
              {remaining != null && endMode !== "temp"
                ? t("heat.boostRemaining", { time: fmtDuration(remaining) })
                : t("heat.boostUntil", { temp: fmtTemp(dev.boost_state?.target_temp_c ?? dev.boost_temp_c) })}
            </span>
          </div>
          <Button size="lg" variant="danger" onClick={toggle} loading={busy}>
            <Icon name="stop" size={16} /> {t("heat.boostStop")}
          </Button>
        </div>
      ) : (
        <>
          <Button size="lg" variant="primary" block onClick={toggle} disabled={!dev.online || boostTooHot(dev)} loading={busy}>
            <Icon name="boost" size={20} />
            <span className="btn-text-2">
              <span>{t("heat.boostStart")}</span>
              <small>{boostPlan(dev, t)}</small>
            </span>
          </Button>
          {boostTooHot(dev) && <p className="field-help">{t("heat.boostTooHot", { temp: fmtTemp(dev.boost_temp_c) })}</p>}
        </>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------ Auto */

function QuickCar({ dev }: { dev: SnapshotDevice }) {
  const { t } = useI18n();
  const confirmed = useMemo(() => ({ override: dev.override ?? null }), [dev.override]);
  const cmd = useDeviceCommand(dev.id, confirmed);
  const override = cmd.shown<string | null>("override", confirmed.override);
  const state = carState(dev);
  const s = carSentence(dev, t);
  const power = Number(dev.data?.power ?? 0);
  const soc = typeof dev.data?.soc === "number" ? (dev.data.soc as number) : null;
  const modeLabel = t(`modes.car.${dev.mode ?? "pv_only"}`);
  const value = override === "fast" ? "fast" : override === "stop" ? "stop" : "auto";

  const choose = async (v: string) => {
    if (v === "fast" && !(await confirmFast(t))) return;
    if (v === "fast") void cmd.send("fast", undefined, { override: "fast" });
    else if (v === "stop") void cmd.send("stop", undefined, { override: "stop" });
    else void cmd.send("auto", undefined, { override: null });
  };

  return (
    <Card className="quick quick-car">
      <div className="quick-head">
        <span className="quick-icon tone-car"><Icon name="car" size={22} /></span>
        <div className="quick-title">
          <h2>{dev.name}</h2>
          <span className="quick-meta num">
            {t(`car.state.${state}`)}{state === "charging" ? ` · ${fmtW(power)}` : ""}{soc != null ? ` · ${fmtPct(soc)}` : ""}
            {dev.override_state?.until ? ` · ${t(dev.override_state.kind === "stop" ? "car.overrideStop" : "car.overrideFast", { until: fmtClock(dev.override_state.until) })}` : ""}
          </span>
        </div>
        <Link to="/auto" className="btn ghost btn-sm quick-more">{t("home.details")} <Icon name="chevron" size={16} /></Link>
      </div>
      <p className={`quick-status tone-${s.tone}`}>{s.text}</p>
      <Segment
        size="lg"
        ariaLabel={t("car.mode")}
        value={value}
        onChange={(v) => void choose(v)}
        pending={cmd.pending("override") ? value : null}
        options={[
          { value: "auto", label: modeLabel, icon: "sun" },
          { value: "fast", label: t("modes.car.fast"), icon: "bolt" },
          { value: "stop", label: t("modes.car.off"), icon: "stop" },
        ]}
      />
    </Card>
  );
}

/* ------------------------------------------------------------ Batterie */

function QuickBattery({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const bat = snapshot.battery;
  const manual = snapshot.battery_manual;
  const remaining = useCountdown(manual?.remaining_s);
  if (!bat) return null;
  const p = bat.power;
  const plan = snapshot.battery_plan;
  const status = manual
    ? `${t(`bat.act.${manual.intent ?? (manual.mode === "force_charge" ? "charge" : "discharge")}`)} · ${t("bat.runningLeft", { time: fmtDuration(remaining) })}`
    : plan && plan.intent !== "auto" ? plan.reason
    : p > 30 ? t("bat.sCharging", { power: fmtW(p) })
    : p < -30 ? t("bat.sDischarging", { power: fmtW(-p) }) : t("bat.sIdle");

  const stop = async () => {
    try {
      await post("/api/battery/manual", { action: "auto" });
      toast.ok(t("bat.stopped"));
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card className="quick quick-battery">
      <div className="quick-head">
        <span className="quick-icon tone-battery"><Icon name="battery" size={22} /></span>
        <div className="quick-title">
          <h2>{t("bat.title")}</h2>
          <span className="quick-meta num">{fmtPct(bat.soc)}</span>
        </div>
        <Link to="/batterie" className="btn ghost btn-sm quick-more">{t("home.details")} <Icon name="chevron" size={16} /></Link>
      </div>
      <div className="soc-bar" role="progressbar" aria-label={t("bat.title")}
           aria-valuenow={Math.round(bat.soc)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`soc-fill ${p > 30 ? "up" : p < -30 ? "down" : ""}`} style={{ width: `${Math.max(2, Math.min(100, bat.soc))}%` }} />
      </div>
      <p className="quick-status">{status}</p>
      {manual ? (
        <ActionButton onClick={stop}><Icon name="auto" size={16} /> {t("bat.auto")}</ActionButton>
      ) : (
        <Button variant="ghost" onClick={() => navigate("/batterie#manuell")}>
          <Icon name="bolt" size={16} /> {t("bat.manualTitle")}
        </Button>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------ Daumenleiste */

/** Die drei Handgriffe, für die man die App am Handy öffnet – unten, im
 *  Daumenbereich, über der Tab-Leiste. Jeder Knopf zeigt sofort, was er
 *  bewirkt hat (optimistisch), und was gerade läuft (Restzeit). */
function ActionBar({ water, car }: { water?: SnapshotDevice; car?: SnapshotDevice }) {
  const { t } = useI18n();
  const wConfirmed = useMemo(() => ({ boost: water?.boost === true }), [water?.boost]);
  const wCmd = useDeviceCommand(water?.id ?? -1, wConfirmed);
  const boost = wCmd.shown("boost", wConfirmed.boost);
  const left = useCountdown(water?.boost ? water.boost_state?.remaining_s : null);
  const cConfirmed = useMemo(() => ({ override: car?.override ?? null }), [car?.override]);
  const cCmd = useDeviceCommand(car?.id ?? -1, cConfirmed);
  const override = cCmd.shown<string | null>("override", cConfirmed.override);
  const plugged = car ? ["charging", "waiting", "asleep", "complete"].includes(carState(car)) : false;
  if (!water && !car) return null;

  return (
    <nav className="action-bar" aria-label={t("home.quick")}>
      {water && (
        <Button className={`action ${boost ? "on" : ""}`} pressed={boost} loading={wCmd.pending("boost")}
                disabled={!water.online || (!boost && boostTooHot(water))}
                onClick={async () => {
                  if (!boost && !(await confirmBoost(water, t))) return;
                  void wCmd.send(boost ? "boost_off" : "boost", undefined, { boost: !boost });
                }}>
          <Icon name="boost" size={20} />
          <span className="action-text">
            <span>{boost ? t("home.actBoostOn") : t("home.actBoost")}</span>
            <small className="num">{boost && left != null ? fmtDuration(left) : fmtTemp(Number(water.data?.temperature_c ?? NaN))}</small>
          </span>
        </Button>
      )}
      {car && (
        <Button className={`action ${override === "fast" ? "on" : ""}`} pressed={override === "fast"}
                loading={cCmd.pending("override")} disabled={!car.online || (!plugged && override !== "fast")}
                onClick={async () => {
                  if (override !== "fast" && !(await confirmFast(t))) return;
                  void cCmd.send(override === "fast" ? "auto" : "fast", undefined,
                                 { override: override === "fast" ? null : "fast" });
                }}>
          <Icon name="bolt" size={20} />
          <span className="action-text">
            <span>{override === "fast" ? t("home.actFastOn") : t("home.actFast")}</span>
            <small>{t(`car.state.${carState(car)}`)}</small>
          </span>
        </Button>
      )}
      {car && (
        <Button className={`action ${override === "stop" ? "on" : ""}`} pressed={override === "stop"}
                loading={cCmd.pending("override")} disabled={!car.online}
                onClick={() => cCmd.send(override === "stop" ? "auto" : "stop", undefined,
                                         { override: override === "stop" ? null : "stop" })}>
          <Icon name={override === "stop" ? "play" : "stop"} size={20} />
          <span className="action-text">
            <span>{override === "stop" ? t("home.actResume") : t("home.actStop")}</span>
            <small>{car.override_state?.until && override === "stop"
              ? t("home.actUntil", { time: fmtClock(car.override_state.until) }) : t("home.actStopHint")}</small>
          </span>
        </Button>
      )}
    </nav>
  );
}

/* ------------------------------------------------------------ Seite */

export function Dashboard() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const isPhone = useIsPhone();
  const snapshot = useLive((s) => s.snapshot);
  const [range, setRange] = useState<(typeof RANGES)[number]>("24h");
  const [stats, setStats] = useState<Statistics | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const hide = (key: string) => setDismissed((d) => [...d, key]);

  useEffect(() => {
    let alive = true;
    const load = () => {
      get<Statistics>("/api/statistics?range=24h").then((s) => alive && setStats(s)).catch(() => {});
      get<Forecast>("/api/statistics/forecast").then((f) => alive && setForecast(f)).catch(() => {});
    };
    load();
    const timer = window.setInterval(load, 60000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  if (!snapshot) {
    return (
      <div className="page">
        <div className="skeleton" style={{ height: 180 }} />
        <div className="skeleton" style={{ height: 340 }} />
      </div>
    );
  }

  const ex = explain(snapshot, t);
  const devices = snapshot.devices.filter((d) => d.enabled);
  const offline = devices.filter((d) => !d.online);
  const failing = devices.filter((d) => d.online && d.command_error);
  const warnings = snapshot.warnings ?? [];
  const car = devices.find((d) => d.category === "wallbox");
  const water = devices.find((d) => d.category === "water_heater");
  const heaters = devices.filter((d) => d.category === "water_heater");
  const grid = snapshot.grid_power ?? 0;
  const offlineKey = `off:${offline.map((d) => d.id).join(",")}`;
  const failingKey = `cmd:${failing.map((d) => `${d.id}=${d.command_error}`).join("|")}`;
  const warnKey = `warn:${warnings.join("|")}`;

  const togglePause = async () => {
    if (!snapshot.paused && !(await confirmTouch({ title: t("confirm.pauseTitle"), icon: "pause", confirmLabel: t("confirm.pause"),
                                                   tone: "danger", text: t("confirm.pauseText") }))) return;
    try {
      await post(snapshot.paused ? "/api/status/resume" : "/api/status/pause");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  };

  if (devices.length === 0) {
    return (
      <div className="page">
        <Card>
          <Empty icon={<Icon name="devices" size={32} />} title={t("home.noDevices")}
                 action={<Button variant="primary" onClick={() => navigate("/wizard")}>{t("home.openWizard")}</Button>}>
            {t("home.noDevicesText")}
          </Empty>
        </Card>
      </div>
    );
  }

  return (
    <div className={`page home ${isPhone ? "has-action-bar" : ""}`}>
      {/* 1 – Was nicht stimmt */}
      {snapshot.paused && (
        <Banner tone="warn" title={t("home.pausedTitle")}
                action={<ActionButton size="sm" variant="primary" onClick={togglePause}><Icon name="play" size={14} /> {t("home.resume")}</ActionButton>}>
          {t("explain.paused")}
        </Banner>
      )}
      {!snapshot.paused && snapshot.safety && (
        <Banner tone="err" title={t("home.safetyTitle")}>{snapshot.safety}</Banner>
      )}
      {offline.length > 0 && !dismissed.includes(offlineKey) && (
        <Banner tone="warn" title={t("home.deviceOffline")} onDismiss={() => hide(offlineKey)} dismissLabel={t("common.close")}>
          {offline.map((d) => d.name).join(", ")}{offline[0].last_error ? ` – ${shortError(offline[0].last_error)}` : ""}
        </Banner>
      )}
      {failing.length > 0 && !dismissed.includes(failingKey) && (
        <Banner tone="err" title={t("home.commandFailed")} onDismiss={() => hide(failingKey)} dismissLabel={t("common.close")}>
          {failing.map((d) => `${d.name}: ${d.command_error}`).join(" · ")}
        </Banner>
      )}
      {warnings.length > 0 && !dismissed.includes(warnKey) && (
        <Banner tone="warn" title={t("home.warnings")} onDismiss={() => hide(warnKey)} dismissLabel={t("common.close")}>
          {warnings.join(" · ")}
        </Banner>
      )}

      {/* 2 – Was passiert, und warum */}
      <section className={`hero tone-${ex.tone}`} aria-labelledby="hero-title">
        <div className="hero-top">
          <h1 id="hero-title" className="hero-title">{ex.headline}</h1>
          <div className="hero-actions">
            <ActionButton size="sm" variant={snapshot.paused ? "primary" : "ghost"} onClick={togglePause}
                          title={snapshot.paused ? t("home.resume") : t("home.pause")}>
              <Icon name={snapshot.paused ? "play" : "pause"} size={14} />
              <span className="btn-label-text">{snapshot.paused ? t("home.resume") : t("home.pause")}</span>
            </ActionButton>
            {!isPhone && (
              <Button size="sm" variant="ghost" onClick={() => navigate("/wall")}>
                <Icon name="fullscreen" size={14} /> {t("home.wall")}
              </Button>
            )}
          </div>
        </div>
        <div className="hero-chips">
          {snapshot.battery && (
            <span className="chip tone-battery"><Icon name="battery" size={16} /> <span className="num">{fmtPct(snapshot.battery.soc)}</span></span>
          )}
          {snapshot.price_ct != null && (
            <span className={`chip ${snapshot.price_cheap ? "tone-pv" : ""}`}>
              <Icon name="price" size={16} /> <span className="num">{fmtCt(snapshot.price_ct)}</span>
              <span className="chip-note">{snapshot.price_cheap ? t("home.priceCheap") : t("home.priceHigh")}</span>
            </span>
          )}
          <span className={`chip ${grid > 50 ? "tone-import" : grid < -50 ? "tone-export" : ""}`}>
            <Icon name="grid" size={16} />
            <span className="num">{fmtW(Math.abs(grid))}</span>
            <span className="chip-note">{grid > 50 ? t("home.import") : grid < -50 ? t("home.export") : t("home.balanced")}</span>
          </span>
        </div>
        {isPhone ? (
          <details className="why-fold">
            <summary>
              <span className="why-first">{ex.reasons[0]?.text ?? ""}</span>
              {ex.reasons.length > 1 && <span className="why-more">{t("home.whyMore", { n: ex.reasons.length - 1 })}</span>}
            </summary>
            <ul className="why-list">
              {ex.reasons.slice(1).map((r) => (
                <li key={r.key} className={`why tone-${r.tone ?? "idle"}`}>
                  <Icon name={r.icon} size={18} />
                  <span>{r.text}</span>
                </li>
              ))}
            </ul>
          </details>
        ) : (
          <>
            <h2 className="why-title">{t("home.why")}</h2>
            <ul className="why-list">
              {ex.reasons.map((r) => (
                <li key={r.key} className={`why tone-${r.tone ?? "idle"}`}>
                  <Icon name={r.icon} size={18} />
                  <span>{r.text}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {/* 3+4 – Fluss und Schnellzugriff */}
      <div className="home-grid">
        <Card className="flow-card">
          <CardHead title={t("home.flow")} />
          <PowerFlow snapshot={snapshot} />
        </Card>
        <div className="home-side">
          {/* Mehrere Geräte je Klasse: jedes bekommt seine Karte */}
          {heaters.map((d) => <QuickWater key={d.id} dev={d} named={heaters.length > 1} />)}
          {devices.filter((d) => d.category === "wallbox").map((d) => <QuickCar key={d.id} dev={d} />)}
          <QuickBattery snapshot={snapshot} />
        </div>
      </div>

      {/* 5 – Kennzahlen */}
      <section className="figures" aria-label={t("home.figures")}>
        <div className="figure">
          <span className="figure-label">{t("home.surplus")}</span>
          <span className={`figure-value num ${snapshot.surplus > 100 ? "t-pv" : ""}`}>{fmtW(Math.max(0, snapshot.surplus))}</span>
          <span className="figure-hint">{t("home.surplusHelp")}</span>
        </div>
        <div className="figure">
          <span className="figure-label">{t("home.wasted")}</span>
          <span className={`figure-value num ${snapshot.waste_w > 100 ? "t-waste" : ""}`}>{fmtW(snapshot.waste_w)}</span>
          <span className="figure-hint">{stats ? t("home.wastedHint", { kwh: fmtKwh(stats.wasted_kwh) }) : " "}</span>
        </div>
        {forecast?.today_kwh != null && (
          <div className="figure">
            <span className="figure-label">{t("home.forecast")}</span>
            <span className="figure-value num t-pv">{fmtKwh(forecast.today_kwh)}</span>
          </div>
        )}
        {snapshot.price_ct != null && (
          <div className="figure">
            <span className="figure-label">{t("home.price")}</span>
            <span className={`figure-value num ${snapshot.price_cheap ? "t-pv" : ""}`}>{fmtCt(snapshot.price_ct)}</span>
            {snapshot.price_limit_ct != null && (
              <span className="figure-hint">{t("tariff.limit", { limit: fmtCt(snapshot.price_limit_ct) })}</span>
            )}
          </div>
        )}
      </section>

      {/* Verlauf: am Handy eingeklappt, damit die Seite kurz bleibt */}
      {isPhone ? (
        <Disclosure title={t("home.history")}>
          <Segment options={RANGES.map((r) => ({ value: r, label: r }))} value={range} onChange={setRange} />
          <PowerHistoryChart range={range} />
        </Disclosure>
      ) : (
        <Card>
          <CardHead title={t("home.history")}>
            <Segment options={RANGES.map((r) => ({ value: r, label: r }))} value={range} onChange={setRange} />
          </CardHead>
          <PowerHistoryChart range={range} />
        </Card>
      )}
      {isPhone && <ActionBar water={water} car={car} />}
    </div>
  );
}
