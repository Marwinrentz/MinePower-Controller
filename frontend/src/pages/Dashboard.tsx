/** Dashboard – der Zustand der Anlage auf einen Blick.
 *
 *  Aufbau nach Wichtigkeit, nicht nach Verfügbarkeit der Daten:
 *   1. Störungen und Sicherheitszustände (falls vorhanden) – ganz oben,
 *      unübersehbar, im Klartext.
 *   2. Kopfzeile: Was macht die Anlage gerade? Überschuss, Netz, Verschenkt.
 *   3. Energiefluss + Gerätekacheln mit direkter Bedienung.
 *   4. Verlauf – Details, die man sucht statt sieht.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PowerHistoryChart } from "../components/charts";
import { DeviceTile } from "../components/DeviceTile";
import { BalanceMeter, FlowBar } from "../components/EnergyBalance";
import { EnergyFlow } from "../components/EnergyFlow";
import {
  AnimatedValue, Badge, Banner, Button, Card, Empty, Metric, Segment, useIsPhone,
} from "../components/ui";
import { useI18n } from "../i18n";
import { get, post } from "../lib/api";
import { fmtW } from "../lib/format";
import type { Snapshot, Statistics } from "../lib/types";
import { useLive } from "../store/live";
import { Icon } from "../components/icons";

const RANGES = ["6h", "24h", "7d", "30d"] as const;
const TILE_ORDER: Record<string, number> = {
  wallbox: 0, water_heater: 1, battery: 2, inverter: 3, meter: 4,
};

type Forecast = { today_kwh?: number | null; active?: boolean };

/** Ein Satz, der den Systemzustand zusammenfasst – die Kernfrage
 *  „läuft das gerade richtig?“ soll man nicht aus Zahlen ableiten müssen. */
function describeState(snapshot: Snapshot, t: (k: string) => string): { headline: string; sub: string } {
  if (snapshot.paused) return { headline: t("dash.statePaused"), sub: t("dash.statePausedSub") };
  if (snapshot.safety) return { headline: t("dash.stateSafety"), sub: snapshot.safety };

  const charging = snapshot.wallbox_power > 50;
  const heating = snapshot.water_power > 50;
  const grid = snapshot.grid_power ?? 0;

  if (charging && heating) return { headline: t("dash.stateBoth"), sub: t("dash.stateSolarSub") };
  if (charging) return { headline: t("dash.stateCharging"), sub: fmtW(snapshot.wallbox_power) };
  if (heating) return { headline: t("dash.stateHeating"), sub: fmtW(snapshot.water_power) };
  if (snapshot.waste_w > 100) return { headline: t("dash.stateExporting"), sub: t("dash.stateExportingSub") };
  if (grid > 100) return { headline: t("dash.stateImporting"), sub: fmtW(grid) };
  return { headline: t("dash.stateIdle"), sub: t("dash.stateIdleSub") };
}

export function Dashboard() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const isPhone = useIsPhone();
  const snapshot = useLive((s) => s.snapshot);
  const connected = useLive((s) => s.connected);
  const [range, setRange] = useState<(typeof RANGES)[number]>("24h");
  const [stats, setStats] = useState<Statistics | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [busy, setBusy] = useState(false);
  // Weggeklickte Meldungen werden über ihren Inhalt gemerkt, nicht über eine
  // laufende Nummer: Ein *neuer* Fehler taucht damit wieder auf, derselbe
  // bleibt weg. Zustände, die weiter gelten (Sicherheits-Stopp, Pause),
  // lassen sich bewusst nicht ausblenden.
  const [dismissed, setDismissed] = useState<string[]>([]);
  const hide = (key: string) => setDismissed((d) => [...d, key]);
  const isHidden = (key: string) => dismissed.includes(key);

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
      <Card>
        <div className="col">
          <div className="skeleton" style={{ height: 26, width: "38%" }} />
          <div className="skeleton" style={{ height: 300 }} />
        </div>
      </Card>
    );
  }

  const grid = snapshot.grid_power ?? 0;
  const state = describeState(snapshot, t);
  const tiles = [...snapshot.devices]
    .filter((d) => d.enabled)
    .sort((a, b) => (TILE_ORDER[a.category] ?? 9) - (TILE_ORDER[b.category] ?? 9));
  const offline = tiles.filter((d) => !d.online);
  const failing = tiles.filter((d) => d.online && d.command_error);
  const offlineKey = `off:${offline.map((d) => d.id).join(",")}`;
  const failingKey = `cmd:${failing.map((d) => `${d.id}=${d.command_error}`).join("|")}`;

  const togglePause = async () => {
    setBusy(true);
    try {
      await post(snapshot.paused ? "/api/status/resume" : "/api/status/pause");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="col" style={{ gap: "var(--s4)" }}>
      {/* 1 – Was gerade nicht stimmt, zuerst. */}
      {snapshot.paused && (
        <Banner tone="warn" title={t("dash.paused")}
                action={<Button small onClick={togglePause} disabled={busy}><Icon name="play" size={14} /> {t("dash.resume")}</Button>}>
          {t("dash.statePausedSub")}
        </Banner>
      )}
      {!snapshot.paused && snapshot.safety && (
        <Banner tone="err" title={t("dash.safetyTitle")}>{snapshot.safety}</Banner>
      )}
      {!connected && (
        <Banner tone="warn" title={t("dash.disconnected")}>{t("dash.disconnectedSub")}</Banner>
      )}
      {offline.length > 0 && !isHidden(offlineKey) && (
        <Banner tone="warn" title={t("dash.deviceOffline")}
                onDismiss={() => hide(offlineKey)} dismissLabel={t("common.close")}>
          {offline.map((d) => d.name).join(", ")}
          {offline[0].last_error ? ` — ${offline[0].last_error}` : ""}
        </Banner>
      )}
      {failing.length > 0 && !isHidden(failingKey) && (
        <Banner tone="err" title={t("dash.commandFailed")}
                onDismiss={() => hide(failingKey)} dismissLabel={t("common.close")}>
          {failing.map((d) => `${d.name}: ${d.command_error}`).join(" · ")}
        </Banner>
      )}

      {/* 2 – Zustand und Kennzahlen */}
      <div className="hero">
        <div className="hero-state">
          <span className="hero-headline">{state.headline}</span>
          <span className="hero-sub">{state.sub}</span>
        </div>

        <div className="metrics">
          <Metric
            label={t("dash.surplus")}
            value={<AnimatedValue value={snapshot.surplus} format={fmtW} />}
            color={snapshot.surplus > 100 ? "var(--c-pv)" : "var(--text-dim)"}
            title={t("dash.surplusHelp")}
          />
          <Metric
            label={grid >= 0 ? t("dash.import") : t("dash.export")}
            value={<AnimatedValue value={Math.abs(grid)} format={fmtW} />}
            color={grid > 30 ? "var(--c-grid-import)" : "var(--c-grid-export)"}
          />
          <Metric
            label={t("dash.wasted")}
            value={<AnimatedValue value={snapshot.waste_w} format={fmtW} />}
            color={snapshot.waste_w > 100 ? "var(--c-waste)" : "var(--text-dim)"}
            hint={stats ? `${stats.wasted_kwh} kWh / 24 h` : undefined}
            title={snapshot.waste_reason ?? t("dash.wastedHelp")}
          />
          {stats && (
            <Metric label={t("stats.pvYield")} hint="24 h" value={`${stats.pv_kwh} kWh`} color="var(--c-pv)" />
          )}
          {/* Auf dem Handy nur die vier Kennzahlen, die im Alltag zählen –
              der Rest steht eine Ebene tiefer in der Statistik. */}
          {!isPhone && stats && <Metric label={t("stats.autarky")} hint="24 h" value={`${stats.autarky_pct} %`} />}
          {!isPhone && forecast?.today_kwh != null && (
            <Metric label={t("settings.forecast")} hint={t("dash.today")}
                    value={`${forecast.today_kwh.toFixed(1)} kWh`} />
          )}
          {!isPhone && snapshot.price_ct != null && (
            <Metric label={t("dash.price")}
                    hint={snapshot.price_cheap == null ? undefined
                      : t(snapshot.price_cheap ? "dash.priceCheap" : "dash.priceNormal")}
                    value={`${snapshot.price_ct.toFixed(1)} ct`} />
          )}
        </div>

        <div className="row tight">
          <Button small variant={snapshot.paused ? "primary" : ""} onClick={togglePause} disabled={busy}>
            <><Icon name={snapshot.paused ? "play" : "pause"} size={14} />{" "}
            {snapshot.paused ? t("dash.resume") : t("dash.pause")}</>
          </Button>
          {!isPhone && (
            <Button small variant="ghost" onClick={() => navigate("/wall")} title={t("wall.openHelp")}>
              <Icon name="fullscreen" size={14} /> {t("wall.open")}
            </Button>
          )}
        </div>
      </div>

      {/* Erklärt die Kennzahl „verschenkt“, sobald sie relevant wird */}
      {snapshot.waste_reason && snapshot.waste_w > 150 && (
        <Banner tone="info" title={`${t("dash.wasted")}: ${fmtW(snapshot.waste_w)}`}>
          {snapshot.waste_reason}
        </Banner>
      )}

      {/* 3 – Fluss und Geräte */}
      <div className="dash-main">
        <Card className="flow-card">
          <div className="card-head" style={{ marginBottom: isPhone ? "var(--s3)" : 0 }}>
            <span className="section-label">{t("dash.energyFlow")}</span>
            <div className="row tight">
              {snapshot.gap_filled_w > 0 && (
                <Badge tone="accent" >{t("dash.gapFilled")}: {fmtW(snapshot.gap_filled_w)}</Badge>
              )}
              {snapshot.weather && <Badge>{snapshot.weather}</Badge>}
            </div>
          </div>
          {/* Zuerst die Kernfrage: steht der Netzpunkt auf null? Erst danach
              die Verteilung – das Knoten-Diagramm am Desktop, die kompakte
              Leiste auf dem Handy (ein 660 px breites SVG ist dort unlesbar). */}
          <BalanceMeter snapshot={snapshot} t={t} size={isPhone ? "md" : "sm"} />
          {isPhone
            ? <div style={{ marginTop: "var(--s3)" }}><FlowBar snapshot={snapshot} t={t} /></div>
            : <EnergyFlow snapshot={snapshot} />}
        </Card>

        <div className="dash-tiles">
          {tiles.map((d, i) => (
            <div key={d.id} className="list-enter" style={{ animationDelay: `${i * 40}ms` }}>
              <DeviceTile device={d} />
            </div>
          ))}
          {tiles.length === 0 && (
            <Card>
              <Empty icon={<Icon name="devices" size={30} />} title={t("dash.noDevices")}>{t("dash.noDevicesSub")}</Empty>
            </Card>
          )}
        </div>
      </div>

      {/* 4 – Verlauf */}
      <Card>
        <div className="card-head">
          <h2>{t("dash.history")}</h2>
          <Segment options={RANGES.map((r) => ({ value: r, label: r }))} value={range} onChange={setRange} />
        </div>
        <PowerHistoryChart range={range} />
      </Card>
    </div>
  );
}
