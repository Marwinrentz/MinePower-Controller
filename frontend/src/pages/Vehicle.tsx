/** Geräteseite: Fahrzeug und Warmwasser groß, mit allem, was der jeweilige
 *  Treiber wirklich kann.
 *
 *  Abgrenzung zur Geräteliste: Dort geht es um Einrichtung — IP, Treiber,
 *  Verbindungstest. Hier geht es um den Betrieb an genau einem Gerät, mit
 *  Platz für Ladestand, Ladezyklen und die herstellerspezifischen Regler.
 *
 *  Grundsatz: Es erscheint nur, was das Gerät auch beherrscht. Die
 *  Ladegrenze steht in `capabilities`, das Wecken ebenso — ein Bedienelement
 *  für etwas, das der Treiber nicht kann, wäre eine Attrappe. Und wo die
 *  Automatik den Wert bestimmt (Ladestrom im PV-Betrieb), steht er als
 *  Messwert samt Begründung da, nicht als Schieber, der gegen den Regler
 *  arbeitet.
 */
import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../i18n";
import { get, post } from "../lib/api";
import { fmtKwh, fmtW } from "../lib/format";
import { useLive } from "../store/live";
import type { SnapshotDevice } from "../lib/types";
import { Badge, Button, Card, Segment } from "../components/ui";
import { Icon } from "../components/icons";
import { CarArt, TankArt } from "../components/DeviceArt";

type Session = {
  id: number;
  device_id: number;
  started_at: string;
  ended_at: string | null;
  energy_kwh: number;
  solar_kwh: number;
  cost_eur: number;
};

const WB_MODES = ["pv_only", "min_pv", "fast", "target", "price"] as const;
const WH_MODES = ["pv_only", "schedule", "off"] as const;

function num(d: SnapshotDevice, key: string): number | null {
  const v = d.data?.[key];
  return typeof v === "number" ? v : null;
}
function str(d: SnapshotDevice, key: string): string | null {
  const v = d.data?.[key];
  return typeof v === "string" ? v : null;
}

/* ------------------------------------------------------------------ Fahrzeug */

function VehicleCard({ dev, onAction }: { dev: SnapshotDevice; onAction: () => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Der Schieber muss dem Finger sofort folgen; gesendet wird erst beim
  // Loslassen. Sonst löst jede Zwischenstufe einen Funkbefehl ans Fahrzeug aus.
  const [limitDraft, setLimitDraft] = useState<number | null>(null);

  const caps = dev.capabilities ?? [];
  const state = str(dev, "state") ?? "idle";
  const power = num(dev, "power") ?? 0;
  const soc = num(dev, "soc");
  const current = num(dev, "current_set");
  const phases = num(dev, "phases_active");
  const session = num(dev, "energy_session_kwh");
  const reachable = dev.data?.vehicle_reachable;
  const limit = dev.charge_limit_soc ?? null;
  const charging = state === "charging" && power > 100;
  const plugged = state !== "idle";
  const reason = typeof dev.decision?.reason === "string" ? dev.decision.reason : null;

  async function act(action: string, value?: unknown) {
    setBusy(action);
    setErr(null);
    try {
      await post(`/api/devices/${dev.id}/action`, { action, value });
      onAction();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const shownLimit = limitDraft ?? limit;

  return (
    <Card className="dev-page">
      <div className="dev-hero">
        <CarArt soc={soc} charging={charging} plugged={plugged} />
        <div className="dev-hero-info">
          <h2>{dev.name}</h2>
          <div className="dev-state">
            <Badge tone={charging ? "ok" : dev.online ? "info" : "warn"}>
              {t(`device.state.${state}`)}
            </Badge>
            {charging && <span className="num dev-power">{fmtW(power)}</span>}
          </div>
          {soc != null && (
            <div className="dev-soc">
              <span className="num">{Math.round(soc)}</span><small>%</small>
            </div>
          )}
          {reason && <p className="dim dev-reason">{reason}</p>}
          {reachable === false && (
            <p className="dev-warn">
              <Icon name="warning" size={14} /> {t("vehicle.asleep")}
            </p>
          )}
        </div>
      </div>

      {/* Live-Messwerte: was gerade wirklich fließt */}
      <div className="dev-readout">
        <div><span>{t("vehicle.current")}</span>
             <b className="num">{current != null ? `${Math.round(current)} A` : "–"}</b></div>
        <div><span>{t("vehicle.phases")}</span>
             <b className="num">{phases ?? "–"}</b></div>
        <div><span>{t("vehicle.power")}</span>
             <b className="num">{fmtW(power)}</b></div>
        <div><span>{t("vehicle.sessionEnergy")}</span>
             <b className="num">{session != null ? fmtKwh(session) : "–"}</b></div>
      </div>

      {/* Betriebsart */}
      <div className="dev-block">
        <label className="section-label">{t("device.mode")}</label>
        <Segment
          options={WB_MODES.map((m) => ({ value: m, label: t(`mode.${m}`) }))}
          value={(dev.mode ?? "pv_only") as (typeof WB_MODES)[number]}
          onChange={(v) => act("set_mode", v)}
          ariaLabel={t("device.mode")}
        />
      </div>

      {/* Manueller Eingriff */}
      <div className="dev-block">
        <label className="section-label">{t("device.override")}</label>
        <div className="row">
          <Button small pressed={dev.override === "fast"} disabled={busy !== null}
                  onClick={() => act("fast")}>{t("device.overrideFast")}</Button>
          <Button small pressed={dev.override === "stop"} disabled={busy !== null}
                  onClick={() => act("stop")}>{t("device.overrideStop")}</Button>
          <Button small pressed={!dev.override} disabled={busy !== null}
                  onClick={() => act("auto")}>{t("device.overrideAuto")}</Button>
          {caps.includes("wake") && (
            <Button small variant="ghost" disabled={busy !== null}
                    title={t("vehicle.wakeHelp")}
                    onClick={() => act("wake")}>
              {busy === "wake" ? t("common.loading") : t("vehicle.wake")}
            </Button>
          )}
        </div>
      </div>

      {/* Ladegrenze – nur wenn der Treiber sie setzen kann */}
      {caps.includes("charge_limit") && (
        <div className="dev-block">
          <div className="dev-block-head">
            <label className="section-label" htmlFor={`lim-${dev.id}`}>
              {t("device.chargeLimit")}
            </label>
            <b className="num">{shownLimit != null ? `${Math.round(shownLimit)} %` : "–"}</b>
          </div>
          <input
            id={`lim-${dev.id}`}
            className="dev-slider"
            /* Ganze Prozent, nicht 5er-Schritte: Das Fahrzeug meldet Werte wie
               82 % zurück. Mit einer gröberen Rasterung rastet der Knopf auf
               80, während Beschriftung und Füllung 82 anzeigen — der Schieber
               widerspricht dann sichtbar seinem eigenen Wert. */
            type="range" min={50} max={100} step={1}
            value={shownLimit ?? 80}
            onChange={(e) => setLimitDraft(Number(e.target.value))}
            onPointerUp={() => {
              if (limitDraft != null && limitDraft !== limit) act("set_charge_limit", limitDraft);
              setLimitDraft(null);
            }}
            onKeyUp={() => {
              if (limitDraft != null && limitDraft !== limit) act("set_charge_limit", limitDraft);
              setLimitDraft(null);
            }}
            style={{ ["--pct" as string]: `${(((shownLimit ?? 80) - 50) / 50) * 100}%` }}
          />
          <p className="dim dev-hint">{t("device.chargeLimitHelp")}</p>
        </div>
      )}

      {err && <p className="dev-err">{err}</p>}
    </Card>
  );
}

/* ---------------------------------------------------------------- Warmwasser */

function HeaterCard({ dev, onAction }: { dev: SnapshotDevice; onAction: () => void }) {
  const { t } = useI18n();
  const [err, setErr] = useState<string | null>(null);

  const power = num(dev, "power") ?? 0;
  const temp = num(dev, "temperature_c");
  const target = 60; // Zielwert steht in den Geräteeinstellungen, nicht im Snapshot
  const heating = power > 50;
  const reason = typeof dev.decision?.reason === "string" ? dev.decision.reason : null;
  const status = dev.data?.extra && typeof dev.data.extra === "object"
    ? (dev.data.extra as Record<string, unknown>)["Gerätestatus"]
    : null;

  async function act(action: string, value?: unknown) {
    setErr(null);
    try {
      await post(`/api/devices/${dev.id}/action`, { action, value });
      onAction();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Card className="dev-page">
      <div className="dev-hero">
        <TankArt temp={temp} target={target} heating={heating} />
        <div className="dev-hero-info">
          <h2>{dev.name}</h2>
          <div className="dev-state">
            <Badge tone={heating ? "ok" : dev.online ? "info" : "warn"}>
              {heating ? t("dash.charging") : t("mode.off")}
            </Badge>
            {heating && <span className="num dev-power">{fmtW(power)}</span>}
          </div>
          {temp != null && (
            <div className="dev-soc">
              <span className="num">{Math.round(temp)}</span><small>°C</small>
            </div>
          )}
          {reason && <p className="dim dev-reason">{reason}</p>}
          {typeof status === "string" && <p className="dim dev-reason">{status}</p>}
        </div>
      </div>

      <div className="dev-readout">
        <div><span>{t("vehicle.power")}</span><b className="num">{fmtW(power)}</b></div>
        <div><span>{t("vehicle.temp")}</span>
             <b className="num">{temp != null ? `${Math.round(temp)} °C` : "–"}</b></div>
      </div>

      <div className="dev-block">
        <label className="section-label">{t("device.mode")}</label>
        <Segment
          options={WH_MODES.map((m) => ({ value: m, label: t(`mode.${m}`) }))}
          value={(dev.mode ?? "pv_only") as (typeof WH_MODES)[number]}
          onChange={(v) => act("set_mode", v)}
          ariaLabel={t("device.mode")}
        />
      </div>

      <div className="dev-block">
        <label className="section-label">{t("device.boost")}</label>
        <div className="row">
          <Button small pressed={dev.boost === true} onClick={() => act("boost")}>
            {t("device.boost")}
          </Button>
          <Button small pressed={dev.boost !== true} onClick={() => act("boost_off")}>
            {t("device.boostOff")}
          </Button>
        </div>
        <p className="dim dev-hint">{t("vehicle.boostHelp")}</p>
      </div>

      {err && <p className="dev-err">{err}</p>}
    </Card>
  );
}

/* -------------------------------------------------------------- Ladezyklen */

function Cycles({ deviceId }: { deviceId: number }) {
  const { t } = useI18n();
  const [rows, setRows] = useState<Session[] | null>(null);

  useEffect(() => {
    get<Session[]>("/api/sessions?limit=40")
      .then((r) => setRows(r.filter((s) => s.device_id === deviceId)))
      .catch(() => setRows([]));
  }, [deviceId]);

  const max = useMemo(
    () => Math.max(1, ...(rows ?? []).map((r) => r.energy_kwh)),
    [rows],
  );

  if (rows == null) return <Card><p className="dim">{t("common.loading")}</p></Card>;
  if (rows.length === 0) return null;

  return (
    <Card>
      <h2>{t("vehicle.cycles")}</h2>
      <p className="dim" style={{ marginBottom: "var(--s4)" }}>{t("vehicle.cyclesHelp")}</p>
      <ul className="cycle-list">
        {rows.map((s) => {
          const solar = s.energy_kwh > 0 ? s.solar_kwh / s.energy_kwh : 0;
          const started = new Date(s.started_at);
          return (
            <li key={s.id} className="cycle">
              <div className="cycle-when">
                <b>{started.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" })}</b>
                <span className="dim">{started.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
              </div>
              {/* Ein Balken, zwei Aussagen: Länge = Energie, grüner Anteil =
                  Solaranteil. Der Netzanteil ist genau das, was fehlt. */}
              <div className="cycle-bar" style={{ width: `${(s.energy_kwh / max) * 100}%` }}>
                <div className="cycle-solar" style={{ width: `${solar * 100}%` }} />
              </div>
              <div className="cycle-nums">
                <b className="num">{fmtKwh(s.energy_kwh)}</b>
                <span className="num dim">{Math.round(solar * 100)} % {t("vehicle.solarShare")}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/* ---------------------------------------------------------------- Seite */

export function Vehicle() {
  const { t } = useI18n();
  const snapshot = useLive((s) => s.snapshot);
  const [tick, setTick] = useState(0);
  const bump = () => setTick((n) => n + 1);

  // Auto und Warmwasser bekommen getrennte Ansichten statt einer langen
  // Seite. Beides untereinander hieß: Wer nach der Wassertemperatur sieht,
  // scrollt jedes Mal an Fahrzeug, Reglern und dreißig Ladezyklen vorbei.
  const loads = (snapshot?.devices ?? []).filter(
    (d) => d.enabled && (d.category === "wallbox" || d.category === "water_heater"),
  );
  const [selected, setSelected] = useState<number | null>(null);
  const active = loads.find((d) => d.id === selected) ?? loads[0];

  if (!snapshot) return <p className="dim">{t("common.loading")}</p>;
  if (loads.length === 0) {
    return (
      <Card>
        <h2>{t("vehicle.none")}</h2>
        <p className="dim">{t("vehicle.noneSub")}</p>
      </Card>
    );
  }

  const isCar = active.category === "wallbox";

  return (
    <div className="stack-lg" key={tick}>
      {loads.length > 1 && (
        <div className="dev-switch">
          {loads.map((d) => (
            <button
              key={d.id}
              type="button"
              className={`dev-switch-item ${d.id === active.id ? "on" : ""}`}
              aria-current={d.id === active.id}
              onClick={() => setSelected(d.id)}
            >
              <Icon name={d.category === "wallbox" ? "car" : "water"} size={17} />
              <span>{d.name}</span>
            </button>
          ))}
        </div>
      )}
      {isCar
        ? <><VehicleCard dev={active} onAction={bump} /><Cycles deviceId={active.id} /></>
        : <HeaterCard dev={active} onAction={bump} />}
    </div>
  );
}
