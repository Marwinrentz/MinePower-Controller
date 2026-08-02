/** Gerätekachel fürs Dashboard: Zustand, Leistung, Bedienung – ohne
 *  Seitenwechsel. Die drei häufigsten Handgriffe (Modus wechseln, Boost,
 *  Sofortladen) sind hier direkt erreichbar, auch auf dem Handy.
 *
 *  Fehlerzustände werden nicht versteckt: Ist ein Gerät offline oder hat es
 *  einen Befehl nicht übernommen, steht das in der Kachel selbst.
 */
import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { post } from "../lib/api";
import { fmtW } from "../lib/format";
import type { SnapshotDevice } from "../lib/types";
import { AnimatedValue, Bar, Badge, Segment, StatusDot, useFlashOnChange, useIsPhone } from "./ui";

const WALLBOX_MODES = ["pv_only", "min_pv", "fast", "target", "price"] as const;
const HEATER_MODES = ["pv_only", "schedule", "off"] as const;

const ICONS: Record<string, string> = {
  wallbox: "🚗", water_heater: "💧", battery: "🔋", inverter: "☀️", meter: "🏭",
};

/** Ladegrenze des Fahrzeugs (nicht der Wallbox).
 *
 *  Dauerhaft auf 100 % zu laden lässt Lithium-Akkus messbar schneller altern;
 *  80 % ist für den Alltag die übliche Empfehlung, volle Ladung hebt man sich
 *  für lange Fahrten auf. Deshalb steht der Regler hier direkt an der Kachel
 *  statt in einem Untermenü.
 *
 *  Der Wert wird erst beim Loslassen gesendet – ein Fahrzeug-Kommando je
 *  Pixel-Bewegung würde die Fahrzeug-API überrennen (und beim BLE-Proxy
 *  schlicht in einer Warteschlange enden). */
function ChargeLimitSlider({ value, onCommit, disabled, t }: {
  value: number; onCommit: (v: number) => void; disabled: boolean; t: (k: string) => string;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const shown = draft ?? value;
  return (
    <div className="limit">
      <div className="limit-head">
        <span className="limit-label">{t("device.chargeLimit")}</span>
        <strong className="limit-value num">{shown} %</strong>
      </div>
      <input
        className="limit-range"
        type="range"
        min={50}
        max={100}
        step={5}
        value={shown}
        disabled={disabled}
        aria-label={t("device.chargeLimit")}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={() => { if (draft !== null && draft !== value) onCommit(draft); setDraft(null); }}
        onKeyUp={() => { if (draft !== null && draft !== value) onCommit(draft); setDraft(null); }}
        onBlur={() => setDraft(null)}
        style={{ ["--limit-pct" as string]: `${((shown - 50) / 50) * 100}%` }}
      />
      <div className="limit-scale" aria-hidden="true">
        <span>50</span><span>80</span><span>100 %</span>
      </div>
    </div>
  );
}
const COLORS: Record<string, string> = {
  wallbox: "var(--c-car)", water_heater: "var(--c-water)", battery: "var(--c-battery)",
  inverter: "var(--c-pv)", meter: "var(--c-house)",
};

export function DeviceTile({ device }: { device: SnapshotDevice }) {
  const { t } = useI18n();
  const isPhone = useIsPhone();
  const data = device.data ?? {};

  // Optimistisches UI: Modus/Override/Boost lokal sofort spiegeln, bis der
  // nächste Snapshot (bis ~3 s später) den echten Zustand bestätigt. Ohne das
  // fühlt sich jeder Tastendruck an, als wäre er verschluckt worden.
  const [optMode, setOptMode] = useState<string | null>(null);
  const [optOverride, setOptOverride] = useState<string | null | undefined>(undefined);
  const [optBoost, setOptBoost] = useState<boolean | null>(null);
  const [optLimit, setOptLimit] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (optMode !== null && device.mode === optMode) setOptMode(null); }, [device.mode, optMode]);
  useEffect(() => { if (optOverride !== undefined && device.override === optOverride) setOptOverride(undefined); }, [device.override, optOverride]);
  useEffect(() => { if (optBoost !== null && !!device.boost === optBoost) setOptBoost(null); }, [device.boost, optBoost]);
  // Das Fahrzeug meldet die neue Grenze erst nach einigen Sekunden zurück –
  // bis dahin zeigt die Kachel den gewünschten Wert.
  useEffect(() => {
    if (optLimit !== null && Number(device.charge_limit_soc) === optLimit) setOptLimit(null);
  }, [device.charge_limit_soc, optLimit]);

  const mode = optMode ?? device.mode;
  const override = optOverride !== undefined ? optOverride : device.override;
  const boost = optBoost !== null ? optBoost : !!device.boost;

  const power = Number(
    device.category === "inverter" ? (data.pv_power ?? 0)
      : device.category === "meter" ? (data.grid_power ?? 0)
        : (data.power ?? 0),
  );
  const active = Math.abs(power) > 50;
  const flash = useFlashOnChange(active);

  const action = async (name: string, value?: unknown) => {
    if (name === "set_mode") setOptMode(String(value));
    else if (name === "fast") setOptOverride("fast");
    else if (name === "stop") setOptOverride("stop");
    else if (name === "auto") setOptOverride(null);
    else if (name === "boost") setOptBoost(true);
    else if (name === "boost_off") setOptBoost(false);
    setPending(true);
    setError(null);
    try {
      await post(`/api/devices/${device.id}/action`, { action: name, value });
    } catch (e) {
      setOptMode(null); setOptOverride(undefined); setOptBoost(null); setOptLimit(null);
      setError(String((e as Error).message));
    } finally {
      setPending(false);
    }
  };

  const statusState = device.online ? "online" : device.last_error ? "error" : "offline";
  const color = COLORS[device.category] ?? "var(--text-strong)";
  const classes = ["tile", active ? "is-active" : "", device.online ? "" : "is-offline",
    flash ? "flash" : ""].filter(Boolean).join(" ");

  return (
    <div className={classes}>
      <div className="tile-head">
        <div className="tile-title">
          <span className="tile-icon" aria-hidden="true">{ICONS[device.category] ?? "⚙️"}</span>
          <span className="tile-name">{device.name}</span>
        </div>
        <StatusDot
          state={statusState}
          title={device.last_error ?? (device.online ? t("common.online") : t("common.offline"))}
        />
      </div>

      {/* Fehler sind Teil der Kachel, nicht ein Log-Eintrag */}
      {!device.online && device.last_error && (
        <span style={{ color: "var(--err)", fontSize: "var(--text-xs)" }}>{device.last_error}</span>
      )}
      {device.command_error && (
        <Badge tone="err">{t("device.notApplied")}: {device.command_error}</Badge>
      )}
      {error && <Badge tone="err">{error}</Badge>}

      {device.category === "wallbox" && (
        <>
          <div className="row between wrap">
            <AnimatedValue className="tile-power" value={power} format={fmtW} style={{ color }} />
            <span className="tile-meta">
              {t(`device.state.${String(data.state ?? "idle")}`)}
              {data.current_set ? ` · ${Number(data.current_set).toFixed(0)} A` : ""}
              {data.phases_active ? ` · ${data.phases_active}⏦` : ""}
            </span>
          </div>
          {data.soc != null && (
            <Bar value={Number(data.soc)} max={100} color={color} label={`${Math.round(Number(data.soc))} %`} />
          )}
          {data.energy_session_kwh != null && Number(data.energy_session_kwh) > 0 && (
            <span className="tile-meta">
              {t("device.session")} {Number(data.energy_session_kwh).toFixed(1)} kWh
            </span>
          )}
          <div className={`tile-controls ${pending ? "busy" : ""}`}>
            <Segment
              ariaLabel={t("device.mode")}
              options={WALLBOX_MODES.map((m) => ({ value: m, label: t(`mode.${m}`) }))}
              value={(mode as (typeof WALLBOX_MODES)[number]) ?? "pv_only"}
              onChange={(m) => action("set_mode", m)}
            />
            {/* Drei gleichbreite Tasten. Am Handy kurze Beschriftungen –
                „Sofort laden" passt in ein Drittel von 360 px schlicht nicht,
                und abgeschnittener Text ist schlechter als ein kurzes Wort. */}
            <div className="tile-actions row tight">
              <button className={`btn small ${override === "fast" ? "pressed" : ""}`}
                      title={t("device.overrideFast")}
                      onClick={() => action("fast")}>
                ⚡ {isPhone ? t("device.overrideFastShort") : t("device.overrideFast")}
              </button>
              <button className={`btn small ${override === "stop" ? "pressed" : ""}`}
                      title={t("device.overrideStop")}
                      onClick={() => action("stop")}>
                ⏹ {t("device.overrideStopShort")}
              </button>
              <button className={`btn small ${!override ? "pressed" : ""}`}
                      title={t("device.overrideAuto")}
                      onClick={() => action("auto")}>
                🤖 {isPhone ? t("device.overrideAutoShort") : t("device.overrideAuto")}
              </button>
            </div>
            {(device.capabilities ?? []).includes("charge_limit") && (
              <ChargeLimitSlider
                value={optLimit ?? Math.round(Number(device.charge_limit_soc ?? 80))}
                disabled={pending}
                t={t}
                onCommit={(v) => { setOptLimit(v); action("set_charge_limit", v); }}
              />
            )}
          </div>
        </>
      )}

      {device.category === "water_heater" && (
        <>
          <div className="row between wrap">
            <AnimatedValue className="tile-power" value={power} format={fmtW} style={{ color }} />
            {data.temperature_c != null && (
              <strong style={{ color }}>{Number(data.temperature_c).toFixed(1)} °C</strong>
            )}
          </div>
          {data.temperature_c != null && (
            <Bar value={Number(data.temperature_c)} max={75} color={color} />
          )}
          <div className={`tile-controls ${pending ? "busy" : ""}`}>
            <Segment
              ariaLabel={t("device.mode")}
              options={HEATER_MODES.map((m) => ({ value: m, label: t(`mode.${m}`) }))}
              value={(mode as (typeof HEATER_MODES)[number]) ?? "pv_only"}
              onChange={(m) => action("set_mode", m)}
            />
            <button className={`btn small ${boost ? "pressed" : ""}`}
                    onClick={() => action(boost ? "boost_off" : "boost")}>
              🔥 {boost ? t("device.boostOff") : t("device.boost")}
            </button>
          </div>
        </>
      )}

      {device.category === "battery" && (
        <>
          <div className="row between wrap">
            <span className="tile-power" style={{ color }}>
              {data.soc != null ? `${Number(data.soc).toFixed(0)} %` : "–"}
            </span>
            <span className="tile-meta">
              {Number(data.power ?? 0) > 30 ? `${t("dash.charging")} ${fmtW(Number(data.power))}`
                : Number(data.power ?? 0) < -30 ? `${t("dash.discharging")} ${fmtW(Math.abs(Number(data.power)))}`
                  : t("dash.batteryIdle")}
            </span>
          </div>
          <Bar value={Number(data.soc ?? 0)} max={100} color={color} />
          <span className="tile-reason">{t("device.batteryPassive")}</span>
        </>
      )}

      {(device.category === "inverter" || device.category === "meter") && (
        <div className="row between wrap">
          <AnimatedValue className="tile-power" value={power} format={fmtW} style={{ color }} />
          {data.daily_yield_kwh != null && (
            <span className="tile-meta">{t("dash.today")} {Number(data.daily_yield_kwh).toFixed(1)} kWh</span>
          )}
        </div>
      )}

      {device.decision?.reason ? (
        <span className="tile-reason">{String(device.decision.reason)}</span>
      ) : null}
    </div>
  );
}
