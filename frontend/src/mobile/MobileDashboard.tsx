/** Handy-Dashboard – eigenständiger Aufbau, kein geschrumpfter Desktop.
 *
 *  Reihenfolge nach dem, was man am Telefon wirklich wissen will:
 *   1. Störungen (falls vorhanden)
 *   2. Steht der Netzpunkt auf null?  ← die Kernfrage, groß und mittig
 *   3. Kennzahlen als *ein* Block, mit der Begründung als Fußnote darunter
 *   4. Wohin fließt die Energie
 *   5. Geräte – als aufklappbare Zeilen, damit die Steuerung erreichbar ist,
 *      ohne dass die Liste zur Wand aus Knöpfen wird
 *
 *  Nur zwei Dinge tragen hier eine eigene Kante: die Waage und die
 *  Kennzahlengruppe. Vorher hatte jede einzelne Kennzahl ihre eigene Karte –
 *  neun schwebende Flächen auf einem Bildschirm, alle gleich laut. Wenn alles
 *  hervorgehoben ist, ist nichts hervorgehoben.
 */
import { useState } from "react";
import { useI18n } from "../i18n";
import { post } from "../lib/api";
import { fmtW } from "../lib/format";
import type { Snapshot, SnapshotDevice } from "../lib/types";
import { useLive } from "../store/live";
import { Icon, CATEGORY_ICON, type IconName } from "../components/icons";

const SCALE_STEPS = [1000, 2000, 3000, 5000, 8000, 12000, 20000, 30000];


const COLOR: Record<string, string> = {
  wallbox: "var(--c-car)", water_heater: "var(--c-water)", battery: "var(--c-battery)",
  inverter: "var(--c-pv)", meter: "var(--c-house)",
};

/* ------------------------------------------------------------------ Waage */

function Balance({ snapshot, t }: { snapshot: Snapshot; t: (k: string) => string }) {
  const grid = snapshot.grid_power ?? 0;
  const band = Math.max(snapshot.deadband_w || 0, 50);
  const scale = SCALE_STEPS.find((s) => s >= Math.abs(grid) * 1.2) ?? SCALE_STEPS[SCALE_STEPS.length - 1];
  const offset = Math.max(-1, Math.min(1, grid / scale));
  const bandShare = Math.min(0.5, band / scale);

  const inBand = Math.abs(grid) <= band;
  const importing = grid > band;
  const tone = inBand ? "ok" : importing ? "import" : "export";
  const label = inBand ? t("dash.balancePerfect") : importing ? t("dash.import") : t("dash.export");

  return (
    <div className={`m-card m-balance m-tone-${tone}`}>
      <div className="m-balance-top">
        <span className="m-balance-cap">{t("dash.balanceTitle")}</span>
        <span className="m-balance-pill">{label}</span>
      </div>

      <div className="m-balance-read">
        <span className="m-balance-val">{inBand ? "0" : fmtW(Math.abs(grid)).replace(/\s?[kW]+$/, "")}</span>
        <span className="m-balance-unit">{inBand ? "W" : Math.abs(grid) >= 1000 ? "kW" : "W"}</span>
      </div>

      <div className="m-balance-track">
        <div className="m-balance-band"
             style={{ left: `${50 - bandShare * 50}%`, width: `${bandShare * 100}%` }} />
        <div className="m-balance-zero" />
        <div className="m-balance-fill"
             style={offset >= 0
               ? { left: "50%", width: `${offset * 50}%` }
               : { right: "50%", width: `${-offset * 50}%` }} />
      </div>

      <div className="m-balance-scale">
        <span>{t("dash.export")}</span>
        <span>0</span>
        <span>{t("dash.import")}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- Flussleiste */

type Seg = { key: string; icon: IconName; watts: number; color: string; label: string };

/** Mindestbreite, damit ein kleiner Zweig sichtbar bleibt – aber sie wird den
 *  großen Segmenten *abgezogen*, nicht obendrauf gelegt. Vorher stand hier ein
 *  schlichtes Math.max(share, 0.08): Bei vier kleinen Zweigen summierten sich
 *  die Breiten auf über 100 %, der Balken lief aus seiner Spur und zeigte
 *  Verhältnisse, die es nicht gab. Ein Balken, der lügt, ist schlechter als
 *  gar keiner. */
const MIN_SHARE = 0.05;

function shares(items: Seg[], scale: number): number[] {
  const raw = items.map((s) => s.watts / scale);
  const lifted = raw.map((r) => Math.max(r, MIN_SHARE));
  const over = lifted.reduce((a, b) => a + b, 0) - raw.reduce((a, b) => a + b, 0);
  const slack = lifted.map((r) => Math.max(0, r - MIN_SHARE));
  const slackSum = slack.reduce((a, b) => a + b, 0);
  return lifted.map((r, i) => (slackSum > 0 ? r - (slack[i] / slackSum) * over : r));
}

function FlowRow({ tag, items, scale }: { tag: string; items: Seg[]; scale: number }) {
  const w = shares(items, scale);
  const sum = items.reduce((a, s) => a + s.watts, 0);
  return (
    <div className="m-flow-row">
      <div className="m-flow-head">
        <span className="m-flow-tag">{tag}</span>
        <span className="m-flow-sum num">{fmtW(sum)}</span>
      </div>
      <div className="m-flow-track">
        {items.length === 0 && <div className="m-flow-empty" />}
        {items.map((s, i) => (
          <div key={s.key} className="m-flow-seg"
               style={{ width: `${w[i] * 100}%`, background: s.color }}
               title={`${s.label}: ${fmtW(s.watts)}`} />
        ))}
      </div>
      {/* Zahlen unter den Balken statt hineingequetscht: In ein 40-px-Segment
          passt kein Wert, und abgeschnittene Ziffern sind schlimmer als eine
          Zeile mehr. */}
      <div className="m-flow-legend">
        {items.map((s) => (
          <span key={s.key} className="m-flow-key">
            <i style={{ background: s.color }} />
            <Icon name={s.icon} size={12} />
            <b className="num">{fmtW(s.watts)}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

function Flow({ snapshot, t }: { snapshot: Snapshot; t: (k: string) => string }) {
  const grid = snapshot.grid_power ?? 0;
  const bat = snapshot.battery?.power ?? 0;
  const sources: Seg[] = ([
    { key: "pv", icon: "sun", watts: snapshot.pv_power, color: "var(--c-pv)", label: t("dash.pv") },
    { key: "imp", icon: "grid", watts: Math.max(0, grid), color: "var(--c-grid-import)", label: t("dash.import") },
    { key: "bo", icon: "battery", watts: Math.max(0, -bat), color: "var(--c-battery)", label: t("dash.battery") },
  ] as Seg[]).filter((s) => s.watts > 20);
  const loads: Seg[] = ([
    { key: "h", icon: "house", watts: snapshot.house_power, color: "var(--c-house)", label: t("dash.house") },
    { key: "c", icon: "car", watts: snapshot.wallbox_power, color: "var(--c-car)", label: t("dash.car") },
    { key: "w", icon: "water", watts: snapshot.water_power, color: "var(--c-water)", label: t("dash.water") },
    { key: "bi", icon: "battery", watts: Math.max(0, bat), color: "var(--c-battery)", label: t("dash.battery") },
    { key: "e", icon: "export", watts: Math.max(0, -grid), color: "var(--c-grid-export)", label: t("dash.export") },
  ] as Seg[]).filter((s) => s.watts > 20);

  const scale = Math.max(
    sources.reduce((a, s) => a + s.watts, 0),
    loads.reduce((a, s) => a + s.watts, 0),
    1,
  );

  return (
    <div className="m-card m-flow">
      <FlowRow tag={t("dash.barSources")} items={sources} scale={scale} />
      <FlowRow tag={t("dash.barLoads")} items={loads} scale={scale} />
    </div>
  );
}

/* ---------------------------------------------------------------- Geräte */

const WB_MODES = ["pv_only", "min_pv", "fast", "target", "price"] as const;
const WH_MODES = ["pv_only", "price", "schedule", "off"] as const;

function DeviceRow({ device, t }: { device: SnapshotDevice; t: (k: string) => string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [limitDraft, setLimitDraft] = useState<number | null>(null);
  const data = device.data ?? {};

  const power = Number(
    device.category === "inverter" ? (data.pv_power ?? 0)
      : device.category === "meter" ? (data.grid_power ?? 0)
        : (data.power ?? 0),
  );
  const color = COLOR[device.category] ?? "var(--m-text)";
  const controllable = device.category === "wallbox" || device.category === "water_heater";
  const modes = device.category === "wallbox" ? WB_MODES : WH_MODES;
  const limit = limitDraft ?? Math.round(Number(device.charge_limit_soc ?? 80));

  const act = async (action: string, value?: unknown) => {
    setBusy(true); setErr(null);
    try {
      await post(`/api/devices/${device.id}/action`, { action, value });
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const sub = !device.online
    ? (device.last_error ?? t("common.offline"))
    : device.category === "wallbox"
      ? `${t(`device.state.${String(data.state ?? "idle")}`)}${data.soc != null ? ` · ${Math.round(Number(data.soc))} %` : ""}`
      : device.category === "water_heater" && data.temperature_c != null
        ? `${Number(data.temperature_c).toFixed(0)} °C`
        : device.category === "battery" && data.soc != null
          ? `${Math.round(Number(data.soc))} %`
          : String(device.decision?.reason ?? "");

  return (
    <div className="m-dev">
      <button className="m-dev-head" onClick={() => controllable && setOpen(!open)}
              aria-expanded={controllable ? open : undefined}>
        <span className="m-dev-icon"><Icon name={CATEGORY_ICON[device.category] ?? "settings"} size={20} /></span>
        <span className="m-dev-main">
          <span className="m-dev-name">{device.name}</span>
          <span className="m-dev-sub" style={!device.online ? { color: "var(--err)" } : undefined}>{sub}</span>
        </span>
        <span className="m-dev-right">
          <span className="m-dev-power" style={{ color }}>
            {device.category === "battery" && data.soc != null
              ? `${Math.round(Number(data.soc))} %`
              : fmtW(power)}
          </span>
        </span>
        {controllable && <span className={`m-dev-chev ${open ? "open" : ""}`} aria-hidden="true">›</span>}
      </button>

      {controllable && (
        <div className={`m-dev-body ${open ? "open" : ""}`}>
          <div className="m-dev-body-inner">
            <div className="m-dev-controls">
              {err && <div className="m-note err"><span className="m-note-txt"><span>{err}</span></span></div>}

              <div className="m-seg" role="tablist">
                {modes.map((m) => (
                  <button key={m} role="tab" aria-selected={device.mode === m}
                          className={device.mode === m ? "on" : ""}
                          disabled={busy}
                          onClick={() => act("set_mode", m)}>
                    {t(`mode.short.${m}`)}
                  </button>
                ))}
              </div>

              {device.category === "wallbox" ? (
                <div className="m-btnrow">
                  <button className={`m-btn ${device.override === "fast" ? "on" : ""}`} disabled={busy}
                          onClick={() => act("fast")}><Icon name="bolt" size={14} /> {t("device.overrideFastShort")}</button>
                  <button className={`m-btn ${device.override === "stop" ? "on" : ""}`} disabled={busy}
                          onClick={() => act("stop")}><Icon name="stop" size={14} /> {t("device.overrideStopShort")}</button>
                  <button className={`m-btn ${!device.override ? "on" : ""}`} disabled={busy}
                          onClick={() => act("auto")}><Icon name="auto" size={14} /> {t("device.overrideAutoShort")}</button>
                </div>
              ) : (
                <button className={`m-btn ${device.boost ? "on" : ""}`} disabled={busy}
                        onClick={() => act(device.boost ? "boost_off" : "boost")}>
                  <Icon name="boost" size={14} /> {device.boost ? t("device.boostOff") : t("device.boost")}
                </button>
              )}

              {(device.capabilities ?? []).includes("charge_limit") && (
                <div className="m-limit">
                  <div className="m-limit-head">
                    <span className="m-limit-lab">{t("device.chargeLimit")}</span>
                    <span className="m-limit-val">{limit} %</span>
                  </div>
                  <input
                    className="m-range" type="range" min={50} max={100} step={5}
                    value={limit} disabled={busy}
                    aria-label={t("device.chargeLimit")}
                    style={{ ["--m-pct" as string]: `${((limit - 50) / 50) * 100}%` }}
                    onChange={(e) => setLimitDraft(Number(e.target.value))}
                    onPointerUp={() => { if (limitDraft !== null) { act("set_charge_limit", limitDraft); setLimitDraft(null); } }}
                    onKeyUp={() => { if (limitDraft !== null) { act("set_charge_limit", limitDraft); setLimitDraft(null); } }}
                  />
                </div>
              )}

              {device.decision?.reason ? (
                <span className="m-dev-sub" style={{ whiteSpace: "normal" }}>
                  {String(device.decision.reason)}
                </span>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ Seite */

export function MobileDashboard() {
  const { t } = useI18n();
  const snapshot = useLive((s) => s.snapshot);
  const connected = useLive((s) => s.connected);
  const [busy, setBusy] = useState(false);
  // Wie am Desktop: Meldungen werden über ihren Inhalt gemerkt, damit ein
  // neuer Fehler wieder auftaucht und derselbe weggeklickt bleibt.
  const [dismissed, setDismissed] = useState<string[]>([]);

  if (!snapshot) {
    return (
      <div className="m-scroll">
        <div className="m-title-bar"><h1 className="m-title">MinePower</h1></div>
        <div className="m-card m-card-pad">{t("common.loading")}</div>
      </div>
    );
  }

  const offline = snapshot.devices.filter((d) => d.enabled && !d.online);
  const failing = snapshot.devices.filter((d) => d.command_error);
  const offlineKey = `off:${offline.map((d) => d.id).join(",")}`;
  const failingKey = `cmd:${failing.map((d) => `${d.id}=${d.command_error}`).join("|")}`;
  const hide = (k: string) => setDismissed((d) => [...d, k]);
  const devices = snapshot.devices.filter((d) => d.enabled);

  const togglePause = async () => {
    setBusy(true);
    try { await post(snapshot.paused ? "/api/status/resume" : "/api/status/pause"); }
    finally { setBusy(false); }
  };

  return (
    <div className="m-scroll">
      <div className="m-title-bar">
        <div>
          <h1 className="m-title">MinePower</h1>
          <span className="m-title-sub">{snapshot.weather ?? ""}</span>
        </div>
        <span className="m-live">
          <span className={`m-live-dot ${connected ? "" : "off"}`} />
          {connected ? "Live" : t("common.connecting")}
        </span>
      </div>

      <div className="m-stagger" style={{ display: "flex", flexDirection: "column", gap: "var(--m-gap)" }}>
        {snapshot.paused && (
          <div className="m-note warn">
            <Icon name="pause" size={16} />
            <span className="m-note-txt">
              <strong>{t("dash.paused")}</strong>
              <span>{t("dash.statePausedSub")}</span>
            </span>
          </div>
        )}
        {!snapshot.paused && snapshot.safety && (
          <div className="m-note err">
            <Icon name="warning" size={16} />
            <span className="m-note-txt">
              <strong>{t("dash.safetyTitle")}</strong>
              <span>{snapshot.safety}</span>
            </span>
          </div>
        )}
        {failing.length > 0 && !dismissed.includes(failingKey) && (
          <div className="m-note err">
            <Icon name="close" size={16} />
            <span className="m-note-txt">
              <strong>{t("dash.commandFailed")}</strong>
              <span>{failing.map((d) => `${d.name}: ${d.command_error}`).join(" · ")}</span>
            </span>
            <button className="m-note-close" onClick={() => hide(failingKey)}
                    aria-label={t("common.close")}><Icon name="close" size={14} /></button>
          </div>
        )}
        {offline.length > 0 && !dismissed.includes(offlineKey) && (
          <div className="m-note warn">
            <Icon name="warning" size={16} />
            <span className="m-note-txt">
              <strong>{t("dash.deviceOffline")}</strong>
              <span>{offline.map((d) => d.name).join(", ")}</span>
            </span>
            <button className="m-note-close" onClick={() => hide(offlineKey)}
                    aria-label={t("common.close")}><Icon name="close" size={14} /></button>
          </div>
        )}

        <Balance snapshot={snapshot} t={t} />

        {/* Ein Block statt vier Karten – siehe .m-stats in mobile.css. */}
        <div className="m-stats">
          <div className="m-stat">
            <span className={`m-stat-val ${snapshot.surplus > 100 ? "pv" : ""}`}>
              {fmtW(snapshot.surplus)}
            </span>
            <span className="m-stat-lab">{t("dash.surplus")}</span>
          </div>
          <div className="m-stat">
            <span className="m-stat-val pv">{fmtW(snapshot.pv_power)}</span>
            <span className="m-stat-lab">{t("dash.pv")}</span>
          </div>
          <div className="m-stat">
            <span className={`m-stat-val ${snapshot.waste_w > 100 ? "waste" : ""}`}>
              {fmtW(snapshot.waste_w)}
            </span>
            <span className="m-stat-lab">{t("dash.wasted")}</span>
          </div>
          <div className="m-stat">
            <span className={`m-stat-val ${snapshot.battery ? "battery" : "none"}`}>
              {snapshot.battery ? `${Math.round(snapshot.battery.soc)} %` : "–"}
            </span>
            <span className="m-stat-lab">{t("dash.battery")}</span>
          </div>

          {/* Dynamischer Tarif: nur sichtbar, wenn wirklich einer konfiguriert
              ist. Über die ganze Breite, weil die Einordnung (günstig/teuer)
              wichtiger ist als die nackte Zahl. */}
          {snapshot.price_ct != null && (
            <div className="m-stat m-stat-wide">
              <span className="row" style={{ alignItems: "baseline", gap: 8 }}>
                <span className={`m-stat-val ${snapshot.price_cheap ? "pv" : ""}`}>
                  {snapshot.price_ct.toFixed(1)} ct
                </span>
                {snapshot.price_cheap != null && (
                  <span className={`m-price-tag ${snapshot.price_cheap ? "cheap" : ""}`}>
                    {t(snapshot.price_cheap ? "dash.priceCheap" : "dash.priceNormal")}
                  </span>
                )}
              </span>
              <span className="m-stat-lab">{t("dash.price")}</span>
            </div>
          )}
        </div>

        {/* Begründung direkt unter der Zahl, die sie erklärt, statt in einem
            eigenen Abschnitt „Verschenkt" weiter unten – dort stünde dieselbe
            Überschrift ein zweites Mal und man müsste den Bezug selbst
            herstellen. */}
        {snapshot.waste_reason && snapshot.waste_w > 150 && (
          <p className="m-footnote">{snapshot.waste_reason}</p>
        )}
      </div>

      <div className="m-section">{t("dash.energyFlow")}</div>
      <Flow snapshot={snapshot} t={t} />

      <div className="m-section">{t("nav.devices")}</div>
      <div className="m-card">
        {devices.map((d) => <DeviceRow key={d.id} device={d} t={t} />)}
        {devices.length === 0 && (
          <div className="m-card-pad">
            <span className="m-dev-sub" style={{ whiteSpace: "normal" }}>{t("dash.noDevicesSub")}</span>
          </div>
        )}
      </div>

      {/* Ohne Abschnittsüberschrift: Die stand vorher als „Pause" über einem
          Knopf, der „Pause" heißt. Eine Zeile, die nur sich selbst ankündigt. */}
      <button className="m-btn m-btn-wide" disabled={busy} onClick={togglePause}>
        <Icon name={snapshot.paused ? "play" : "pause"} size={15} />{" "}
        {snapshot.paused ? t("dash.resume") : t("dash.pause")}
      </button>
    </div>
  );
}
