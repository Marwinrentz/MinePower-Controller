/** Wand-Ansicht ('Kiosk") – für ein Tablet im Flur oder einen Monitor an der Wand.
 *
 *  Entwurfsprinzip: Diese Seite wird nicht bedient, sondern *im Vorbeigehen
 *  gelesen*. Aus drei Metern Entfernung, oft im Augenwinkel. Daraus folgt
 *  alles Weitere:
 *
 *  • **Eine Aussage, nicht zehn Zahlen.** Ganz oben steht in Klartext, was die
 *    Anlage gerade tut. Wer nur das liest, weiß Bescheid.
 *  • **Die Kernfrage zuerst.** Die Netzpunkt-Waage (BalanceMeter) zeigt mit
 *    einem festen Nullpunkt und sichtbarem Zielband, ob die Anlage gerade
 *    sauber auf null regelt – das ist die eigentliche Frage, nicht 'wie viel
 *    läuft wo". Die Verteilungsleiste daneben beantwortet dann das Wohin.
 *  • **Der Zustand färbt den Rahmen.** Läuft alles auf Sonne, ist der Rand
 *    grün; geht Überschuss ins Netz verloren, wird er bernstein; bei einer
 *    Störung rot. Diese Information erreicht einen, bevor man hinsieht.
 *  • **Schrift skaliert mit dem Bildschirm** (clamp + vw), damit dieselbe
 *    Seite auf einem 8"-Tablet und einem 43"-Monitor gleich gut lesbar ist.
 *  • **Kein Einschlafen, kein Blenden.** Wake-Lock hält den Bildschirm wach,
 *    nachts wird automatisch abgedunkelt.
 *
 *  Erreichbar unter /wall – bewusst außerhalb der normalen Navigation, damit
 *  ein Wandgerät genau diese eine URL öffnet und sonst nichts.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BalanceMeter, WallBars } from "../components/EnergyBalance";
import { AnimatedValue, useAnimatedNumber } from "../components/ui";
import { useI18n } from "../i18n";
import { explain, wasteCauses } from "../lib/explain";
import { fmtW } from "../lib/format";
import type { Snapshot, SnapshotDevice } from "../lib/types";
import { useLive } from "../store/live";
import { Icon, CATEGORY_ICON } from "../components/icons";

/** Bildschirm wach halten, solange die Wand-Ansicht offen ist.
 *  Ohne das schaltet sich ein Tablet nach zwei Minuten ab – und ein
 *  Wand-Dashboard, das man erst aufwecken muss, ist keins. */
function useWakeLock(active: boolean) {
  useEffect(() => {
    if (!active) return;
    let sentinel: { release: () => Promise<void> } | null = null;
    let cancelled = false;

    const request = async () => {
      try {
        const nav = navigator as Navigator & {
          wakeLock?: { request: (type: "screen") => Promise<{ release: () => Promise<void> }> };
        };
        if (!nav.wakeLock) return;
        const lock = await nav.wakeLock.request("screen");
        if (cancelled) { void lock.release(); return; }
        sentinel = lock;
      } catch {
        /* Browser verweigert (kein HTTPS, kein Support) – dann eben nicht. */
      }
    };

    void request();
    // Nach Tab-Wechsel oder Standby geht der Lock verloren und muss zurück.
    const onVisible = () => { if (document.visibilityState === "visible") void request(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
      void sentinel?.release().catch(() => {});
    };
  }, [active]);
}

/** Nachts dunkler: ein Wandgerät im Flur soll um 23 Uhr nicht blenden. */
function useNightMode(): boolean {
  const [night, setNight] = useState(() => {
    const h = new Date().getHours();
    return h >= 22 || h < 6;
  });
  useEffect(() => {
    const timer = window.setInterval(() => {
      const h = new Date().getHours();
      setNight(h >= 22 || h < 6);
    }, 60000);
    return () => window.clearInterval(timer);
  }, []);
  return night;
}

function useClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 10000);
    return () => window.clearInterval(timer);
  }, []);
  return now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** Gesamtzustand → Rahmenfarbe und Kernaussage. Reihenfolge = Dringlichkeit. */
function overallState(s: Snapshot, t: (k: string, v?: Record<string, string | number>) => string) {
  const offline = s.devices.filter((d) => d.enabled && !d.online);
  const failed = s.devices.filter((d) => d.command_error);
  if (s.safety) return { tone: "err", headline: t("dash.stateSafety"), sub: s.safety };
  if (s.paused) return { tone: "warn", headline: t("dash.statePaused"), sub: t("dash.statePausedSub") };
  if (failed.length) return { tone: "err", headline: t("dash.commandFailed"), sub: failed.map((d) => d.name).join(", ") };
  if (offline.length) return { tone: "warn", headline: t("dash.deviceOffline"), sub: offline.map((d) => d.name).join(", ") };
  if (s.waste_w > 150) {
    // Kurz: nur der wichtigste Grund, in Alltagssprache (Details: Diagnose)
    const cause = s.waste_reason ? wasteCauses(s.waste_reason, t)[0] : null;
    return { tone: "waste", headline: t("dash.stateExporting"), sub: cause ?? t("dash.stateExportingSub") };
  }
  // Sonst dieselbe Kernaussage wie die Übersicht – Wand und Handy sollen
  // nie zwei verschiedene Geschichten erzählen.
  const ex = explain(s, t);
  const busy = s.wallbox_power > 50 || s.water_power > 50;
  return { tone: busy ? "ok" : "idle", headline: ex.headline, sub: ex.reasons[0]?.text ?? "" };
}

function WallTile({ device, t }: { device: SnapshotDevice; t: (k: string) => string }) {
  const data = device.data ?? {};
  const colors: Record<string, string> = {
    wallbox: "var(--c-car)", water_heater: "var(--c-water)", battery: "var(--c-battery)",
    inverter: "var(--c-pv)", meter: "var(--c-house)",
  };
  const power = Number(
    device.category === "inverter" ? (data.pv_power ?? 0)
      : device.category === "meter" ? (data.grid_power ?? 0)
        : (data.power ?? 0),
  );
  const soc = data.soc != null ? Number(data.soc) : null;
  const animatedSoc = useAnimatedNumber(soc ?? 0);
  const color = colors[device.category] ?? "var(--text-strong)";
  const active = Math.abs(power) > 50;

  return (
    <div className={`wall-tile ${active ? "active" : ""} ${device.online ? "" : "offline"}`}>
      <div className="wall-tile-top">
        <span className="wall-tile-icon"><Icon name={CATEGORY_ICON[device.category] ?? "settings"} size={22} /></span>
        <span className="wall-tile-name">{device.name}</span>
      </div>

      {device.category === "battery" && soc != null ? (
        <>
          <span className="wall-tile-value num" style={{ color }}>{Math.round(animatedSoc)} %</span>
          <div className="wall-soc">
            <div className="wall-soc-fill" style={{ width: `${Math.max(0, Math.min(100, animatedSoc))}%`, background: color }} />
          </div>
        </>
      ) : (
        <AnimatedValue className="wall-tile-value" value={power} format={fmtW} style={{ color }} />
      )}

      <span className="wall-tile-sub">
        {!device.online
          ? t("common.offline")
          : device.category === "wallbox" && soc != null
            ? `${Math.round(animatedSoc)} % · ${t(`device.state.${String(data.state ?? "idle")}`)}`
            : device.category === "water_heater" && data.temperature_c != null
              ? `${Number(data.temperature_c).toFixed(0)} °C`
              : device.category === "battery"
                ? (Number(data.power ?? 0) > 30 ? t("dash.charging")
                  : Number(data.power ?? 0) < -30 ? t("dash.discharging") : t("dash.batteryIdle"))
                : " "}
      </span>
    </div>
  );
}

export function Wall() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const snapshot = useLive((s) => s.snapshot);
  const connected = useLive((s) => s.connected);
  const { connect, disconnect } = useLive();
  const night = useNightMode();
  const clock = useClock();
  useWakeLock(true);

  useEffect(() => {
    connect();
    return () => disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Escape führt zurück – ein Wandgerät hat oft keine sichtbare Navigation.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") navigate("/"); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    else void document.documentElement.requestFullscreen().catch(() => {});
  };

  if (!snapshot) {
    return (
      <div className="wall wall-idle">
        <span className="wall-headline">{t("common.loading")}</span>
      </div>
    );
  }

  const state = overallState(snapshot, t);
  const tiles = [...snapshot.devices]
    .filter((d) => d.enabled && ["wallbox", "water_heater", "battery"].includes(d.category))
    .slice(0, 4);

  return (
    <div className={`wall tone-${state.tone} ${night ? "night" : ""}`}>
      <header className="wall-head">
        <div className="wall-head-main">
          <span className="wall-headline">{state.headline}</span>
          <span className="wall-sub">{state.sub}</span>
        </div>
        <div className="wall-head-side">
          <span className="wall-clock num">{clock}</span>
          <div className="wall-actions">
            {!connected && <span className="wall-warn"><Icon name="warning" size={15} /> {t("dash.disconnected")}</span>}
            <button className="wall-btn" onClick={toggleFullscreen} title={t("wall.fullscreen")}><Icon name="fullscreen" size={17} /></button>
            <button className="wall-btn" onClick={() => navigate("/")} title={t("wall.exit")}><Icon name="close" size={17} /></button>
          </div>
        </div>
      </header>

      <div className="wall-main">
        {/* Links: die Kernaussage – steht der Netzpunkt auf null? */}
        <div className="wall-col">
          <BalanceMeter snapshot={snapshot} t={t} size="lg" />
          <section className="wall-kpis">
            <div className="wall-kpi">
              <AnimatedValue className="wall-kpi-value" value={snapshot.pv_power} format={fmtW}
                             style={{ color: "var(--c-pv)" }} />
              <span className="wall-kpi-label">{t("dash.pv")}</span>
            </div>
            <div className="wall-kpi">
              <AnimatedValue className="wall-kpi-value" value={snapshot.surplus} format={fmtW}
                             style={{ color: snapshot.surplus > 100 ? "var(--c-pv)" : "var(--text-dim)" }} />
              <span className="wall-kpi-label">{t("dash.surplus")}</span>
            </div>
            <div className="wall-kpi">
              <AnimatedValue className="wall-kpi-value" value={snapshot.waste_w} format={fmtW}
                             style={{ color: snapshot.waste_w > 100 ? "var(--c-waste)" : "var(--text-dim)" }} />
              <span className="wall-kpi-label">{t("dash.wasted")}</span>
            </div>
            <div className="wall-kpi">
              <span className="wall-kpi-value num"
                    style={{ color: snapshot.battery ? "var(--c-battery)" : "var(--text-dim)" }}>
                {snapshot.battery ? `${Math.round(snapshot.battery.soc)} %` : "–"}
              </span>
              <span className="wall-kpi-label">{t("dash.battery")}</span>
            </div>
            {/* Nur bei konfiguriertem dynamischem Tarif – sonst stünde hier
                dauerhaft ein Strich und die Kachel wäre verschenkter Platz. */}
            {snapshot.price_ct != null && (
              <div className="wall-kpi">
                <span className="wall-kpi-value num"
                      style={{ color: snapshot.price_cheap ? "var(--c-pv)" : "var(--text-dim)" }}>
                  {snapshot.price_ct.toFixed(1)} ct
                </span>
                <span className="wall-kpi-label">
                  {t("dash.price")}
                  {snapshot.price_cheap != null &&
                    ` · ${t(snapshot.price_cheap ? "dash.priceCheap" : "dash.priceNormal")}`}
                </span>
              </div>
            )}
          </section>
        </div>

        {/* Rechts: wohin die Energie geht */}
        <div className="wall-col">
          <div className="wall-panel flow">
            <WallBars snapshot={snapshot} t={t} />
          </div>
          {tiles.length > 0 && (
            <section className="wall-tiles">
              {tiles.map((d) => <WallTile key={d.id} device={d} t={t} />)}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
