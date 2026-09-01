/** Detailansicht einer steuerbaren Last – gemeinsame Bausteine für die Seiten
 *  „Auto" und „Warmwasser".
 *
 *  **Sofortige Rückmeldung.** Ein Tastendruck schaltet die Anzeige unverzüglich
 *  um, ohne auf das Gerät zu warten. Das ist keine Kosmetik: Zwischen Befehl
 *  und Bestätigung liegen hier bis zu 15 Sekunden Regeltakt, bei einem
 *  Fahrzeug über Funk noch mehr. Wer so lange nichts sieht, drückt ein zweites
 *  Mal.
 *
 *  Bewusst *nicht* umgesetzt: den bestätigten Zustand kräftiger einfärben.
 *  Dann sähe der Normalzustand — der die meiste Zeit auf dem Schirm steht —
 *  dauerhaft blass und vorläufig aus. Stattdessen ist der gewählte Zustand
 *  sofort voll ausgeprägt, und *solange die Bestätigung aussteht* läuft ein
 *  dünner Balken unter der Bedienzeile. Er verschwindet, sobald das Gerät den
 *  Zustand meldet. Vorübergehendes wird vorübergehend gezeigt, der Ruhezustand
 *  bleibt die Wahrheit.
 *
 *  Schlägt der Befehl fehl, springt die Anzeige auf den echten Zustand zurück
 *  und nennt den Grund — eine Anzeige, die eine Änderung behauptet, die nie
 *  ankam, wäre schlimmer als gar keine Rückmeldung.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { get, post } from "../lib/api";
import { fmtKwh, fmtW } from "../lib/format";
import type { SnapshotDevice } from "../lib/types";
import { Badge, Button, Card, Segment } from "./ui";
import { Icon } from "./icons";
import { CarArt, TankArt } from "./DeviceArt";

export type Session = {
  id: number;
  device_id: number;
  started_at: string;
  ended_at: string | null;
  energy_kwh: number;
  solar_kwh: number;
  cost_eur: number;
};

const WB_MODES = ["pv_only", "min_pv", "fast", "target", "price"] as const;
const WH_MODES = ["pv_only", "price", "schedule", "off"] as const;

/** Falls ein Gerät eine Bestätigung nie liefert (Funk weg, Treiber stumm),
 *  darf die Anzeige nicht dauerhaft etwas Falsches behaupten. */
const CONFIRM_TIMEOUT_MS = 20_000;

function num(d: SnapshotDevice, key: string): number | null {
  const v = d.data?.[key];
  return typeof v === "number" ? v : null;
}
function str(d: SnapshotDevice, key: string): string | null {
  const v = d.data?.[key];
  return typeof v === "string" ? v : null;
}

/* --------------------------------------------------------- Optimistik-Haken */

type Sender = {
  /** Anzuzeigender Wert: der gewünschte, solange er aussteht, sonst der echte. */
  shown: <T>(wanted: T | undefined, real: T) => T;
  send: (action: string, value: unknown, optimistic?: Record<string, unknown>) => void;
  want: Record<string, unknown>;
  pending: boolean;
  error: string | null;
  dismissError: () => void;
};

/** Hält gewünschte Zustände, bis der Snapshot sie bestätigt. */
function useSender(dev: SnapshotDevice, confirmed: Record<string, unknown>): Sender {
  const [want, setWant] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const timers = useRef<Record<string, number>>({});

  // Bestätigt der Snapshot den Wunsch, ist der Wunsch erledigt.
  useEffect(() => {
    const done = Object.keys(want).filter((k) => Object.is(want[k], confirmed[k]));
    if (done.length === 0) return;
    setWant((w) => {
      const next = { ...w };
      for (const k of done) {
        delete next[k];
        window.clearTimeout(timers.current[k]);
      }
      return next;
    });
  }, [confirmed, want]);

  useEffect(() => () => Object.values(timers.current).forEach(window.clearTimeout), []);

  const send = (action: string, value: unknown, optimistic: Record<string, unknown> = {}) => {
    setError(null);
    setWant((w) => ({ ...w, ...optimistic }));
    for (const k of Object.keys(optimistic)) {
      window.clearTimeout(timers.current[k]);
      timers.current[k] = window.setTimeout(() => {
        setWant((w) => {
          const next = { ...w };
          delete next[k];
          return next;
        });
      }, CONFIRM_TIMEOUT_MS);
    }
    void post(`/api/devices/${dev.id}/action`, { action, value }).catch((e) => {
      setWant((w) => {
        const next = { ...w };
        for (const k of Object.keys(optimistic)) delete next[k];
        return next;
      });
      setError(e instanceof Error ? e.message : String(e));
    });
  };

  return {
    want,
    pending: Object.keys(want).length > 0,
    error,
    dismissError: () => setError(null),
    shown: <T,>(wanted: T | undefined, real: T) => (wanted === undefined ? real : wanted),
    send,
  };
}

/* ------------------------------------------------------------- Bausteine */

/** Dünner Laufbalken unter einer Bedienzeile, solange die Bestätigung fehlt. */
function Pending({ on }: { on: boolean }) {
  return <div className={`ld-pending ${on ? "on" : ""}`} aria-hidden="true" />;
}

/** Betriebsarten erklären: Info-Schalter neben der Auswahl, darunter ein Feld
 *  mit einem Satz je Betriebsart. Als aufklappbares Feld statt Tooltip, weil
 *  ein Tooltip auf dem Touchscreen nicht erreichbar wäre. */
function ModeInfo({ modes, active, texts = "help" }: {
  modes: readonly string[]; active?: string;
  /** Welche Erklärungen: „help" für Ladepunkte, „helpWater" für Heizstäbe.
   *  Ein Speicher lädt nicht, er heizt – dieselben Sätze für beide wären falsch. */
  texts?: "help" | "helpWater";
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="ld-info-btn" aria-expanded={open}
              onClick={() => setOpen((o) => !o)}
              title={t("vehicle.modeInfo")}>
        <Icon name="warning" size={13} /> {t("vehicle.modeInfo")}
      </button>
      {!open && active && (
        <p className="ld-hint">{t(`mode.${texts}.${active}`)}</p>
      )}
      {open && (
        <ul className="ld-modes">
          {modes.map((m) => (
            <li key={m} className={m === active ? "on" : ""}>
              <b>{t(`mode.${m}`)}</b>
              <span>{t(`mode.${texts}.${m}`)}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function Head({ name, badge, tone, power, big, unit, note, warn }: {
  name: string; badge: string; tone: "ok" | "info" | "warn";
  power?: number; big?: number | null; unit?: string; note?: string | null; warn?: string | null;
}) {
  return (
    <div className="ld-info">
      {/* title-Attribut: Gerätenamen sind lang ("my-PV AC•THOR / AC ELWA-E /
          ELWA 2") und werden gekürzt; der volle Name bleibt beim Zeigen lesbar. */}
      <h2 className="ld-name" title={name}>{name}</h2>
      <div className="ld-state">
        <Badge tone={tone}>{badge}</Badge>
        {power != null && power > 50 && <span className="num ld-power">{fmtW(power)}</span>}
      </div>
      {big != null && (
        <div className="ld-big"><span className="num">{Math.round(big)}</span><small>{unit}</small></div>
      )}
      {note && <p className="dim ld-note" title={note}>{note}</p>}
      {warn && <p className="ld-warn"><Icon name="warning" size={14} /> {warn}</p>}
    </div>
  );
}

function Readout({ items }: { items: { label: string; value: string }[] }) {
  return (
    <div className="ld-readout">
      {items.map((i) => (
        <div key={i.label}><span>{i.label}</span><b className="num">{i.value}</b></div>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------------- Auto */

export function VehicleDetail({ dev }: { dev: SnapshotDevice }) {
  const { t } = useI18n();
  const caps = dev.capabilities ?? [];
  const state = str(dev, "state") ?? "idle";
  const power = num(dev, "power") ?? 0;
  const soc = num(dev, "soc");
  const current = num(dev, "current_set");
  const phases = num(dev, "phases_active");
  const session = num(dev, "energy_session_kwh");
  const reachable = dev.data?.vehicle_reachable;
  const charging = state === "charging" && power > 100;
  const plugged = state !== "idle";
  const reason = typeof dev.decision?.reason === "string" ? dev.decision.reason : null;

  const confirmed = useMemo(
    () => ({ mode: dev.mode ?? "pv_only", override: dev.override ?? null,
             limit: dev.charge_limit_soc == null ? null : Math.round(Number(dev.charge_limit_soc)) }),
    [dev.mode, dev.override, dev.charge_limit_soc],
  );
  const s = useSender(dev, confirmed);
  const mode = s.shown(s.want.mode as string | undefined, confirmed.mode);
  const override = s.shown(s.want.override as string | null | undefined, confirmed.override);
  const limit = s.shown(s.want.limit as number | null | undefined, confirmed.limit);

  // Ziehen darf dem Finger nicht hinterherhinken; gesendet wird beim Loslassen.
  const [drag, setDrag] = useState<number | null>(null);
  const slider = drag ?? limit ?? 80;

  return (
    <Card className="ld">
      <div className="ld-hero">
        <CarArt soc={soc} charging={charging} plugged={plugged} />
        <Head name={dev.name}
              badge={t(`device.state.${state}`)}
              tone={charging ? "ok" : dev.online ? "info" : "warn"}
              power={power} big={soc} unit="%" note={reason}
              warn={reachable === false ? t("vehicle.asleep") : null} />
      </div>

      <Readout items={[
        { label: t("vehicle.current"), value: current != null ? `${Math.round(current)} A` : "–" },
        { label: t("vehicle.phases"), value: phases != null ? String(phases) : "–" },
        { label: t("vehicle.power"), value: fmtW(power) },
        { label: t("vehicle.sessionEnergy"), value: session != null ? fmtKwh(session) : "–" },
      ]} />

      <section className="ld-block">
        <label className="section-label">{t("device.mode")}</label>
        <Segment
          options={WB_MODES.map((m) => ({ value: m, label: t(`mode.${m}`) }))}
          value={mode as (typeof WB_MODES)[number]}
          onChange={(v) => s.send("set_mode", v, { mode: v })}
          ariaLabel={t("device.mode")}
        />
        <Pending on={s.want.mode !== undefined} />
        <ModeInfo modes={WB_MODES} active={mode} />
      </section>

      <section className="ld-block">
        <label className="section-label">{t("device.override")}</label>
        <div className="ld-row">
          <Button small pressed={override === "fast"}
                  onClick={() => s.send("fast", undefined, { override: "fast" })}>
            <Icon name="bolt" size={14} /> {t("device.overrideFast")}
          </Button>
          <Button small pressed={override === "stop"}
                  onClick={() => s.send("stop", undefined, { override: "stop" })}>
            <Icon name="stop" size={14} /> {t("device.overrideStop")}
          </Button>
          <Button small pressed={!override}
                  onClick={() => s.send("auto", undefined, { override: null })}>
            <Icon name="auto" size={14} /> {t("device.overrideAuto")}
          </Button>
          {caps.includes("wake") && (
            <Button small variant="ghost" title={t("vehicle.wakeHelp")}
                    onClick={() => s.send("wake", undefined)}>
              {t("vehicle.wake")}
            </Button>
          )}
        </div>
        <Pending on={s.want.override !== undefined} />
      </section>

      {caps.includes("charge_limit") && (
        <section className="ld-block">
          <div className="ld-block-head">
            <label className="section-label" htmlFor={`lim-${dev.id}`}>{t("device.chargeLimit")}</label>
            <b className="num">{Math.round(slider)} %</b>
          </div>
          <input id={`lim-${dev.id}`} className="ld-slider" type="range"
                 min={50} max={100} step={1} value={slider}
                 onChange={(e) => setDrag(Number(e.target.value))}
                 onPointerUp={() => {
                   if (drag != null && drag !== limit) s.send("set_charge_limit", drag, { limit: drag });
                   setDrag(null);
                 }}
                 onKeyUp={() => {
                   if (drag != null && drag !== limit) s.send("set_charge_limit", drag, { limit: drag });
                   setDrag(null);
                 }}
                 style={{ ["--pct" as string]: `${((slider - 50) / 50) * 100}%` }} />
          <Pending on={s.want.limit !== undefined} />
          <p className="ld-hint">{t("device.chargeLimitHelp")}</p>
        </section>
      )}

      {s.error && <Badge tone="err">{s.error}</Badge>}
    </Card>
  );
}

/* ----------------------------------------------------------- Warmwasser */

export function WaterDetail({ dev }: { dev: SnapshotDevice }) {
  const { t } = useI18n();
  const power = num(dev, "power") ?? 0;
  const temp = num(dev, "temperature_c");
  const target = dev.target_temp_c ?? 60;
  const boostTemp = dev.boost_temp_c ?? 65;
  // Über der Boost-Grenze verwirft der Regler den Boost sofort wieder.
  // Eine Taste anzubieten, die nachweislich nichts bewirkt, ist schlechter
  // als eine gesperrte mit Begründung.
  const boostPointless = temp != null && temp >= boostTemp;
  const heating = power > 50;
  const reason = typeof dev.decision?.reason === "string" ? dev.decision.reason : null;
  const extra = dev.data?.extra;
  const status = extra && typeof extra === "object"
    ? (extra as Record<string, unknown>)["Gerätestatus"] : null;

  const confirmed = useMemo(
    () => ({ mode: dev.mode ?? "pv_only", boost: dev.boost === true }),
    [dev.mode, dev.boost],
  );
  const s = useSender(dev, confirmed);
  const mode = s.shown(s.want.mode as string | undefined, confirmed.mode);
  const boost = s.shown(s.want.boost as boolean | undefined, confirmed.boost);

  return (
    <Card className="ld">
      <div className="ld-hero">
        <TankArt temp={temp} target={target} heating={heating} />
        <Head name={dev.name}
              badge={heating ? t("water.heating") : t("water.standby")}
              tone={heating ? "ok" : dev.online ? "info" : "warn"}
              power={power} big={temp} unit="°C"
              note={reason ?? (typeof status === "string" ? status : null)} />
      </div>

      <Readout items={[
        { label: t("vehicle.power"), value: fmtW(power) },
        { label: t("vehicle.temp"), value: temp != null ? `${Math.round(temp)} °C` : "–" },
        { label: t("water.target"), value: `${target} °C` },
      ]} />

      <section className="ld-block">
        <label className="section-label">{t("device.mode")}</label>
        <Segment
          options={WH_MODES.map((m) => ({ value: m, label: t(`mode.${m}`) }))}
          value={mode as (typeof WH_MODES)[number]}
          onChange={(v) => s.send("set_mode", v, { mode: v })}
          ariaLabel={t("device.mode")}
        />
        <Pending on={s.want.mode !== undefined} />
        <ModeInfo modes={WH_MODES} active={mode} texts="helpWater" />
      </section>

      <section className="ld-block">
        <label className="section-label">{t("device.boost")}</label>
        <div className="ld-row">
          <Button small variant={boost ? "primary" : ""} pressed={boost}
                  disabled={boostPointless && !boost}
                  title={boostPointless && !boost ? t("water.boostTooHot") : t("vehicle.boostHelp")}
                  onClick={() => s.send(boost ? "boost_off" : "boost", undefined, { boost: !boost })}>
            <Icon name="boost" size={14} /> {boost ? t("device.boostOff") : t("device.boost")}
          </Button>
        </div>
        <Pending on={s.want.boost !== undefined} />
        <p className="ld-hint">
          {boostPointless && !boost
            ? `${t("water.boostTooHot")} (${Math.round(boostTemp)} °C)`
            : t("vehicle.boostHelp")}
        </p>
      </section>

      {s.error && <Badge tone="err">{s.error}</Badge>}
    </Card>
  );
}

/* ------------------------------------------------------------ Ladezyklen */

export function Cycles({ deviceId }: { deviceId: number }) {
  const { t } = useI18n();
  const [rows, setRows] = useState<Session[] | null>(null);

  useEffect(() => {
    get<Session[]>("/api/sessions?limit=40")
      .then((r) => setRows(r.filter((x) => x.device_id === deviceId)))
      .catch(() => setRows([]));
  }, [deviceId]);

  const max = useMemo(() => Math.max(1, ...(rows ?? []).map((r) => r.energy_kwh)), [rows]);
  if (rows == null || rows.length === 0) return null;

  return (
    <Card>
      <h2>{t("vehicle.cycles")}</h2>
      <p className="dim ld-hint" style={{ marginBottom: "var(--s5)" }}>{t("vehicle.cyclesHelp")}</p>
      <ul className="cycle-list">
        {rows.map((r) => {
          const solar = r.energy_kwh > 0 ? r.solar_kwh / r.energy_kwh : 0;
          const started = new Date(r.started_at);
          return (
            <li key={r.id} className="cycle">
              <div className="cycle-when">
                <b>{started.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" })}</b>
                <span className="dim">{started.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
              </div>
              <div className="cycle-track">
                <div className="cycle-bar" style={{ width: `${(r.energy_kwh / max) * 100}%` }}>
                  <div className="cycle-solar" style={{ width: `${solar * 100}%` }} />
                </div>
              </div>
              <div className="cycle-nums">
                <b className="num">{fmtKwh(r.energy_kwh)}</b>
                <span className="num dim">{Math.round(solar * 100)} % {t("vehicle.solarShare")}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/* ------------------------------------------------------------- Leerfall */

export function NoDevice({ titleKey, subKey }: { titleKey: string; subKey: string }) {
  const { t } = useI18n();
  return (
    <Card>
      <h2>{t(titleKey)}</h2>
      <p className="dim">{t(subKey)}</p>
    </Card>
  );
}

/** Umschalter, falls mehrere Geräte derselben Art eingerichtet sind. */
export function DevicePicker({ devices, active, onPick }: {
  devices: SnapshotDevice[]; active: number; onPick: (id: number) => void;
}) {
  if (devices.length < 2) return null;
  return (
    <div className="ld-switch">
      {devices.map((d) => (
        <button key={d.id} type="button" title={d.name}
                className={`ld-switch-item ${d.id === active ? "on" : ""}`}
                aria-current={d.id === active}
                onClick={() => onPick(d.id)}>
          <span>{d.name}</span>
        </button>
      ))}
    </div>
  );
}
