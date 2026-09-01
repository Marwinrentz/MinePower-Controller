/** Dashboard – der Zustand der Anlage auf einen Blick.
 *
 *  Aufbau nach Wichtigkeit, nicht nach Verfügbarkeit der Daten:
 *   1. Störungen und Sicherheitszustände – ganz oben, im Klartext.
 *   2. Der Zustand als ein Satz, groß, ohne Rahmen.
 *   3. Kennzahlen mit klarer Rangfolge: der Überschuss führt, der Rest
 *      ordnet sich unter.
 *   4. Zwei Spalten bis zum Seitenende – links, was die Anlage tut
 *      (Energiefluss jetzt, Verlauf rückblickend), rechts die Geräte, an
 *      denen man etwas ändern kann.
 *
 *  Was hier bewusst fehlt: der Farbverlauf über der Kopfzeile. Er benutzte
 *  die Energiefarben (PV, Auto, Warmwasser) als Dekoration, obwohl dieselben
 *  Farben überall sonst eine feste Bedeutung tragen – dieselbe Farbe darf
 *  nicht zweierlei heißen. Und sieben gleich große Zahlen nebeneinander sind
 *  keine Übersicht: Ohne Rangfolge findet das Auge keinen Einstieg.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PowerHistoryChart } from "../components/charts";
import { DeviceTile } from "../components/DeviceTile";
import { BalanceMeter, FlowBar } from "../components/EnergyBalance";
import { EnergyFlow } from "../components/EnergyFlow";
import {
  AnimatedValue, Badge, Banner, Button, Card, Empty, Figure, Segment, useIsPhone,
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
    <div className="page">
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

      {/* 2 – Der Zustand als ein Satz. Kein Kasten: Er ist die Überschrift
              der Seite, nicht eines ihrer Elemente. */}
      <header className="dash-status">
        <div className="dash-state">
          <h1 className="dash-headline">{state.headline}</h1>
          <p className="dash-sub">{state.sub}</p>
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
      </header>

      {/* 3 – Kennzahlen mit Rangfolge: der Überschuss doppelt breit und
              deutlich größer, alles Weitere gleichrangig darunter. */}
      <div className="figures-block">
        <div className="figures">
          <Figure lead title={t("dash.surplusHelp")} label={t("dash.surplus")}
                  tone={snapshot.surplus > 100 ? "pv" : undefined}
                  value={<AnimatedValue value={snapshot.surplus} format={fmtW} />} />
          <Figure label={grid >= 0 ? t("dash.import") : t("dash.export")}
                  tone={grid > 30 ? "import" : "export"}
                  value={<AnimatedValue value={Math.abs(grid)} format={fmtW} />} />
          <Figure title={snapshot.waste_reason ?? t("dash.wastedHelp")} label={t("dash.wasted")}
                  tone={snapshot.waste_w > 100 ? "waste" : undefined}
                  value={<AnimatedValue value={snapshot.waste_w} format={fmtW} />}
                  hint={stats ? `${stats.wasted_kwh} kWh / 24 h` : undefined} />
          {/* PV-Ertrag und Autarkiegrad standen hier ebenfalls und sind wieder
              verschwunden: Beides sind Rückblicke über 24 Stunden, und beide
              haben auf der Statistikseite eine eigene, größere Bühne. Das
              Dashboard beantwortet „was ist gerade" – die aktuelle PV-Leistung
              steht ohnehin im Flussdiagramm darunter. */}
          {!isPhone && forecast?.today_kwh != null && (
            <Figure label={t("settings.forecast")} value={`${forecast.today_kwh.toFixed(1)} kWh`}
                    hint={t("dash.today")} />
          )}
          {!isPhone && snapshot.price_ct != null && (
            <Figure label={t("dash.price")} value={`${snapshot.price_ct.toFixed(1)} ct`}
                    hint={snapshot.price_cheap != null
                      ? t(snapshot.price_cheap ? "dash.priceCheap" : "dash.priceNormal")
                      : undefined} />
          )}
        </div>

        {/* Erklärt die Kennzahl „verschenkt", sobald sie relevant wird.
            Vorher stand hier ein blauer Banner in voller Breite, der Titel und
            Wert der Kennzahl direkt darüber noch einmal wiederholte – ein
            Kasten quer über die Seite für einen Halbsatz. Jetzt eine Fußnote
            am Raster: gleicher Inhalt, kein zweites Objekt. */}
        {snapshot.waste_reason && snapshot.waste_w > 150 && (
          <p className="figures-note">{snapshot.waste_reason}</p>
        )}
      </div>

      {/* 4 – Zwei Spalten bis zum Seitenende: links, was die Anlage tut
              (Fluss jetzt, Verlauf rückblickend), rechts, woran man drehen
              kann. Der Verlauf stand vorher darunter über die volle Breite –
              dadurch endete die linke Spalte auf halber Höhe und neben den
              Gerätekacheln klaffte ein leeres Feld von der Größe des
              Flussdiagramms. */}
      <div className="dash-main">
        <div className="dash-col">
          <section className="page-section">
            <div className="sec-head">
              <span className="section-label">{t("dash.energyFlow")}</span>
              <div className="row tight">
                {snapshot.gap_filled_w > 0 && (
                  <Badge tone="accent">{t("dash.gapFilled")}: {fmtW(snapshot.gap_filled_w)}</Badge>
                )}
                {snapshot.weather && <Badge>{snapshot.weather}</Badge>}
              </div>
            </div>
            <div className="flow-panel">
              {/* Zuerst die Kernfrage: steht der Netzpunkt auf null? Erst danach
                  die Verteilung – das Knoten-Diagramm am Desktop, die kompakte
                  Leiste auf dem Handy (ein 680 px breites SVG ist dort unlesbar). */}
              <BalanceMeter snapshot={snapshot} t={t} size={isPhone ? "md" : "sm"} />
              {isPhone ? <FlowBar snapshot={snapshot} t={t} /> : <EnergyFlow snapshot={snapshot} />}
            </div>
          </section>

          {/* 5 – Verlauf: Details, die man sucht statt sieht. */}
          <section className="page-section">
            <div className="sec-head">
              <span className="section-label">{t("dash.history")}</span>
              <Segment options={RANGES.map((r) => ({ value: r, label: r }))} value={range} onChange={setRange} />
            </div>
            <Card><PowerHistoryChart range={range} /></Card>
          </section>
        </div>

        <section className="page-section">
          <div className="sec-head">
            <span className="section-label">{t("nav.devices")}</span>
          </div>
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
        </section>
      </div>
    </div>
  );
}
