/** Statistik & Reporting.
 *
 *  Neben den klassischen Kennzahlen steht hier 'Verschenkt' gleichberechtigt:
 *  Autarkie sagt, wie viel Netzstrom man vermieden hat – verschenkte Energie
 *  sagt, wie viel man hätte vermeiden können. Erst zusammen ergibt das ein
 *  ehrliches Bild der Regelgüte. Deshalb sind genau diese beiden die
 *  Leitzahlen; die übrigen sechs ordnen sich darunter ein.
 *
 *  Vorher standen hier acht gleich große, gleich stark eingefärbte Kacheln in
 *  zwei Reihen zu vier – acht Objekte mit acht Rahmen, von denen keines den
 *  Vorrang hatte. Jetzt trägt ein Raster mit Haarlinien alle acht Zahlen, und
 *  Farbe bleibt den Energiepfaden vorbehalten (PV grün, Netzbezug rot,
 *  Einspeisung blau, Ladung türkis, verschenkt orange). Prozentsätze und
 *  Eurobeträge sind keine Energiepfade und stehen neutral.
 */
import { useEffect, useState } from "react";
import { EnergyBalanceChart, WasteChart } from "../components/charts";
import { Banner, Button, Card, Empty, Figure, Help, Segment } from "../components/ui";
import { useI18n } from "../i18n";
import { get } from "../lib/api";
import { fmtTime } from "../lib/format";
import type { ChargeSession, Statistics as Stats } from "../lib/types";
import { useAuth } from "../store/auth";
import { Icon } from "../components/icons";

const RANGES = ["24h", "7d", "30d", "365d"] as const;

async function downloadFile(url: string, filename: string) {
  const token = useAuth.getState().token;
  const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  if (!resp.ok) throw new Error(await resp.text());
  const blob = await resp.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function Statistics() {
  const { t } = useI18n();
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  const [range, setRange] = useState<(typeof RANGES)[number]>("30d");
  const [stats, setStats] = useState<Stats | null>(null);
  const [sessions, setSessions] = useState<ChargeSession[]>([]);
  const [reportError, setReportError] = useState("");
  const [reportBusy, setReportBusy] = useState(false);

  useEffect(() => {
    get<Stats>(`/api/statistics?range=${range}`).then(setStats).catch(() => {});
    get<ChargeSession[]>("/api/sessions?limit=50").then(setSessions).catch(() => {});
  }, [range]);

  const downloadCsv = () => downloadFile(`/api/statistics/export.csv?range=${range}`, `minepower_sessions_${range}.csv`);

  const downloadDiagnosticReport = async () => {
    setReportBusy(true);
    setReportError("");
    try {
      await downloadFile(
        `/api/system/diagnostic-report?range=${range}`,
        `minepower_diagnose_${range}.json`,
      );
    } catch (e) {
      setReportError(String((e as Error).message));
    } finally {
      setReportBusy(false);
    }
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1 className="page-title">{t("stats.title")}</h1>
        <div className="row tight wrap">
          <Segment options={RANGES.map((r) => ({ value: r, label: t(`stats.range.${r}`) }))}
                   value={range} onChange={setRange} />
          {isAdmin && (
            <Button small onClick={downloadDiagnosticReport} disabled={reportBusy}
                    title={t("stats.diagnosticReportHelp")}>
              {t("stats.diagnosticReport")}
            </Button>
          )}
        </div>
      </header>

      {reportError && <Banner tone="err" title={t("common.error")}>{reportError}</Banner>}

      {stats && (
        <section className="page-section">
          <div className="figures">
            <Figure lead label={t("stats.autarky")} value={`${stats.autarky_pct} %`}
                    hint={`${t("stats.selfConsumption")} ${stats.self_consumption_pct} %`} />
            <Figure lead label={t("stats.wasted")} value={`${stats.wasted_kwh} kWh`}
                    tone={stats.wasted_kwh > 0 ? "waste" : undefined}
                    help={t("stats.wastedHelp")}
                    hint={`${stats.wasted_pct} % · ${t("stats.wastedValue")} ${stats.wasted_value_eur.toFixed(2)} €`} />
            <Figure label={t("stats.savings")} value={`${stats.savings_eur.toFixed(2)} €`} />
            <Figure label={t("stats.pvYield")} value={`${stats.pv_kwh} kWh`} tone="pv" />
            <Figure label={t("stats.gridImport")} value={`${stats.grid_import_kwh} kWh`} tone="import" />
            <Figure label={t("stats.gridExport")} value={`${stats.grid_export_kwh} kWh`} tone="export" />
            <Figure label={t("stats.charged")} value={`${stats.charged_kwh} kWh`} tone="car"
                    hint={`${t("stats.solarShare")}: ${stats.charged_solar_pct} %`} />
          </div>
        </section>
      )}

      <section className="page-section">
        <div className="sec-head">
          <span className="section-label">{t("dash.history")}</span>
        </div>
        <div className="grid cols-2">
          <Card>
            <h2>kWh</h2>
            <EnergyBalanceChart range={range === "24h" ? "7d" : range} />
          </Card>
          <Card>
            <div className="card-head">
              <h2>{t("stats.wasted")}</h2>
              <Help text={t("stats.wastedHelp")} />
            </div>
            <WasteChart range={range} />
          </Card>
        </div>
      </section>

      <section className="page-section">
        <div className="sec-head">
          <span className="section-label">{t("stats.sessions")}</span>
          <Button small onClick={downloadCsv}>{t("stats.downloadCsv")}</Button>
        </div>
        <Card>
          {sessions.length === 0 ? (
            <Empty icon={<Icon name="car" size={30} />} title={t("stats.noSessions")} />
          ) : (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("stats.colStart")}</th><th>{t("stats.colEnd")}</th><th>kWh</th><th>{t("stats.colSolar")}</th><th>%</th><th>€</th><th>RFID</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.id}>
                      <td>{fmtTime(s.started_at)}</td>
                      <td>{s.ended_at ? fmtTime(s.ended_at) : "…"}</td>
                      <td className="num">{s.energy_kwh.toFixed(1)}</td>
                      <td className="num">{s.solar_kwh.toFixed(1)}</td>
                      <td className="num">{s.energy_kwh > 0 ? Math.round((100 * s.solar_kwh) / s.energy_kwh) : 0}</td>
                      <td className="num">{s.cost_eur.toFixed(2)}</td>
                      <td>{s.rfid_tag ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}
