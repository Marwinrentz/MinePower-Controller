/** Statistik & Reporting.
 *
 *  Neben den klassischen Kennzahlen steht hier „Verschenkt“ gleichberechtigt:
 *  Autarkie sagt, wie viel Netzstrom man vermieden hat – verschenkte Energie
 *  sagt, wie viel man hätte vermeiden können. Erst zusammen ergibt das ein
 *  ehrliches Bild der Regelgüte.
 */
import { useEffect, useState } from "react";
import { EnergyBalanceChart, WasteChart } from "../components/charts";
import { Banner, Button, Card, Empty, Help, Segment } from "../components/ui";
import { useI18n } from "../i18n";
import { get } from "../lib/api";
import { fmtTime } from "../lib/format";
import type { ChargeSession, Statistics as Stats } from "../lib/types";
import { useAuth } from "../store/auth";
import { Icon } from "../components/icons";

const RANGES = ["24h", "7d", "30d", "365d"] as const;

function StatCard({ label, value, sub, color, help }: {
  label: string; value: string; sub?: string; color?: string; help?: string;
}) {
  return (
    <Card>
      <div className="stat-tile">
        <span className="stat-value num" style={{ color: color ?? "var(--text-strong)" }}>{value}</span>
        <span className="stat-label">
          {label}
          {help && <Help text={help} />}
        </span>
        {sub && <small>{sub}</small>}
      </div>
    </Card>
  );
}

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
    <div className="col" style={{ gap: "var(--s5)" }}>
      <div className="card-head">
        <h1>{t("stats.title")}</h1>
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
      </div>

      {reportError && <Banner tone="err" title={t("common.error")}>{reportError}</Banner>}

      {stats && (
        <>
          <div className="grid cols-4">
            <StatCard label={t("stats.autarky")} value={`${stats.autarky_pct} %`} color="var(--c-pv)" />
            <StatCard label={t("stats.selfConsumption")} value={`${stats.self_consumption_pct} %`} color="var(--c-pv)" />
            <StatCard label={t("stats.savings")} value={`${stats.savings_eur.toFixed(2)} €`} color="var(--ok)" />
            <StatCard
              label={t("stats.wasted")}
              value={`${stats.wasted_kwh} kWh`}
              sub={`${stats.wasted_pct} % · ${t("stats.wastedValue")} ${stats.wasted_value_eur.toFixed(2)} €`}
              color={stats.wasted_kwh > 0 ? "var(--c-waste)" : "var(--text-dim)"}
              help={t("stats.wastedHelp")}
            />
          </div>
          <div className="grid cols-4">
            <StatCard label={t("stats.pvYield")} value={`${stats.pv_kwh} kWh`} color="var(--c-pv)" />
            <StatCard label={t("stats.gridImport")} value={`${stats.grid_import_kwh} kWh`} color="var(--c-grid-import)" />
            <StatCard label={t("stats.gridExport")} value={`${stats.grid_export_kwh} kWh`} color="var(--c-grid-export)" />
            <StatCard label={t("stats.charged")} value={`${stats.charged_kwh} kWh`}
                      sub={`${t("stats.solarShare")}: ${stats.charged_solar_pct} %`} color="var(--c-car)" />
          </div>
        </>
      )}

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

      <Card>
        <div className="card-head">
          <h2>{t("stats.sessions")}</h2>
          <Button small onClick={downloadCsv}>{t("stats.downloadCsv")}</Button>
        </div>
        {sessions.length === 0 ? (
          <Empty icon={<Icon name="car" size={30} />} title={t("stats.noSessions")} />
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Start</th><th>Ende</th><th>kWh</th><th>Solar</th><th>%</th><th>€</th><th>RFID</th>
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
    </div>
  );
}
