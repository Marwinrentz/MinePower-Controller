/** Layoutpruefstand – nur fuer die Entwicklung, nicht Teil der App.
 *
 *  Mountet die echten Seiten mit einem erfundenen Snapshot, damit sich das
 *  Layout ansehen laesst, ohne sich anzumelden oder echte Geraete zu bemuehen.
 *  Wird ueber /preview.html aufgerufen und liegt bewusst ausserhalb des
 *  Routers der App.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "./i18n";
import type { Snapshot, SnapshotDevice } from "./lib/types";
import { useLive } from "./store/live";
import { Dashboard } from "./pages/Dashboard";
import { Statistics } from "./pages/Statistics";
import { MobileDashboard } from "./mobile/MobileDashboard";
import { CarArt, TankArt } from "./components/DeviceArt";
import "./theme.css";
import "./mobile/mobile.css";

/* --- Netzantworten fuer die Seiten, die beim Mounten etwas nachladen --- */
const CANNED: Record<string, unknown> = {
  "/api/statistics": {
    autarky_pct: 74, self_consumption_pct: 62, savings_eur: 231.4,
    wasted_kwh: 18.2, wasted_pct: 4, wasted_value_eur: 1.46,
    pv_kwh: 412.8, grid_import_kwh: 138.5, grid_export_kwh: 96.1,
    charged_kwh: 187.3, charged_solar_pct: 81,
  },
  "/api/statistics/forecast": { today_kwh: 24.6, tomorrow_kwh: 19.2 },
  "/api/history": [],
  "/api/sessions": [],
};
const realFetch = window.fetch.bind(window);
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(typeof input === "string" ? input : (input as Request).url);
  // Laengster Treffer zuerst, sonst faengt /api/statistics auch
  // /api/statistics/forecast ab.
  const hit = Object.keys(CANNED).sort((a, b) => b.length - a.length)
    .find((k) => url.includes(k));
  if (url.includes("/api/") && hit) {
    return new Response(JSON.stringify(CANNED[hit]), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  }
  if (url.includes("/api/")) return new Response("[]", { status: 200 });
  return realFetch(input as RequestInfo, init);
}) as typeof window.fetch;

/* --------------------------------------------------------------- Snapshot */

const dev = (d: Partial<SnapshotDevice> & { id: number; name: string }): SnapshotDevice => ({
  category: "wallbox", driver_id: "sim", enabled: true, online: true,
  last_error: null, command_error: null, decision: {}, ...d,
} as SnapshotDevice);

const SNAP: Snapshot = {
  time: new Date().toISOString(),
  pv_power: 6420, grid_power: -180, house_power: 740,
  wallbox_power: 4600, water_power: 1200,
  battery: { soc: 68, power: 320 },
  surplus: 1820, price_ct: 21.4, price_cheap: true,
  safety: null, paused: false,
  waste_w: 180, waste_reason: "Fahrzeug bereits auf Ladegrenze, Speicher auf Zieltemperatur.",
  gap_filled_w: 240, deadband_w: 120, weather: "Heiter, 22 °C",
  devices: [
    dev({
      id: 1, name: "Wallbox Garage", category: "wallbox", mode: "pv_only",
      capabilities: ["charge_limit"], charge_limit_soc: 82,
      data: { power: 4600, soc: 54, state: "charging", current: 20 },
      decision: { reason: "Laedt mit Ueberschuss, 20 A auf drei Phasen." },
    }),
    dev({
      id: 2, name: "Warmwasserspeicher Keller", category: "water_heater", mode: "pv_only",
      target_temp_c: 60, boost_temp_c: 65,
      data: { power: 1200, temperature_c: 47, heating: true },
      decision: { reason: "Heizt mit Restueberschuss." },
    }),
    dev({ id: 3, name: "Hausspeicher", category: "battery", data: { power: 320, soc: 68 } }),
    dev({ id: 4, name: "Netzzaehler", category: "meter", data: { grid_power: -180 } }),
    dev({ id: 5, name: "Wechselrichter Dach", category: "inverter", data: { pv_power: 6420 } }),
  ],
};

useLive.setState({ snapshot: SNAP, connected: true, stale: false, connect() {}, disconnect() {} });

/* ------------------------------------------------------------------ Seite */

const page = new URLSearchParams(location.search).get("p") ?? "dash";
const theme = new URLSearchParams(location.search).get("theme") ?? "light";
document.documentElement.dataset.theme = theme;

// Fuer Bildschirmfotos: Einblend-Animationen abschalten, sonst erwischt der
// Ausloeser einzelne Kacheln noch bei Deckkraft 0 und im Bild klafft eine
// Luecke, die es im Browser nie gibt.
if (new URLSearchParams(location.search).has("still")) {
  const css = document.createElement("style");
  css.textContent = "*, *::before, *::after { animation: none !important; transition: none !important; }";
  document.head.append(css);
}

function ArtSheet() {
  const rows: Array<[string, number | null, boolean]> = [
    ["leer", 8, false], ["halb", 52, true], ["voll", 96, true], ["unbekannt", null, false],
  ];
  return (
    <div className="page">
      <h1 className="page-title">Geraetegrafiken</h1>
      <div className="grid cols-4">
        {rows.map(([label, v, live]) => (
          <div key={label} className="card">
            <span className="section-label">Fahrzeug · {label}</span>
            <CarArt soc={v} charging={live} plugged={live} />
          </div>
        ))}
        {rows.map(([label, v, live]) => (
          <div key={`t${label}`} className="card">
            <span className="section-label">Speicher · {label}</span>
            <TankArt temp={v == null ? null : 15 + (v / 100) * 45} target={60} heating={live} />
          </div>
        ))}
      </div>
    </div>
  );
}

const PAGES: Record<string, () => JSX.Element> = {
  dash: Dashboard,
  stats: Statistics,
  art: ArtSheet,
  mobile: MobileDashboard,
};
const Page = PAGES[page] ?? Dashboard;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <MemoryRouter>
        {page === "mobile"
          ? <div className="m-app"><Page /></div>
          : (
            <div className="main-content" style={{ padding: 24, maxWidth: 1180, margin: "0 auto" }}>
              <Page />
            </div>
          )}
      </MemoryRouter>
    </I18nProvider>
  </StrictMode>,
);
