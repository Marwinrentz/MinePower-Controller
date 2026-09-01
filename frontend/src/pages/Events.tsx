/** Ereignis-Historie (Audit-Log) + Diagnose.
 *
 *  Die Diagnose zeigt zuerst die Regelgüte in lesbarer Form (Totband,
 *  Wetterlage, verschenkte Leistung samt Begründung) und erst darunter die
 *  Rohdaten. Wer hier landet, sucht meist eine Antwort auf „warum lädt es
 *  gerade nicht?“ – nicht ein JSON-Dokument.
 */
import { useEffect, useState } from "react";
import { Badge, Button, Card, Empty } from "../components/ui";
import { useI18n } from "../i18n";
import { get } from "../lib/api";
import { fmtTime } from "../lib/format";
import type { AppEvent } from "../lib/types";
import { Icon } from "../components/icons";

type Diagnostics = {
  deadband_effective_w?: number;
  deadband_configured_w?: number;
  weather?: string;
  waste_w?: number | null;
  waste_reason?: string | null;
  gap_filled_w?: number | null;
  grid_smoothed?: number;
  paused?: boolean;
  devices?: { id: number; name: string; online: boolean; last_error: string | null;
              command_error: string | null; decision: Record<string, unknown> }[];
};

function DiagValue({ label, value, tone }: { label: string; value: string; tone?: "warn" | "ok" }) {
  return (
    <div className="col" style={{ gap: 0 }}>
      <span className="num" style={{
        color: tone === "warn" ? "var(--c-waste)" : "var(--text-strong)",
        fontSize: "var(--text-lg)", fontWeight: 700,
      }}>{value}</span>
      <span className="metric-label">{label}</span>
    </div>
  );
}

export function Events() {
  const { t } = useI18n();
  const [events, setEvents] = useState<AppEvent[]>([]);
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [raw, setRaw] = useState(false);

  const load = () => {
    get<AppEvent[]>("/api/events?limit=200").then(setEvents).catch(() => {});
  };
  useEffect(load, []);

  // Solange die Diagnose offen ist, aktuell halten – sie ist ein Live-Werkzeug.
  useEffect(() => {
    if (!diag) return;
    const timer = window.setInterval(
      () => get<Diagnostics>("/api/system/diagnostics").then(setDiag).catch(() => {}),
      5000,
    );
    return () => window.clearInterval(timer);
  }, [diag !== null]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadDiag = () => get<Diagnostics>("/api/system/diagnostics").then(setDiag).catch(() => {});
  const tone = (level: string) => (level === "error" ? "err" : level === "warning" ? "warn" : "ok");

  return (
    <div className="page">
      <header className="page-head">
        <h1 className="page-title">{t("events.title")}</h1>
        <div className="row tight">
          <Button small onClick={load} title={t("common.loading")}>⟳</Button>
          <Button small pressed={diag != null} onClick={() => (diag ? setDiag(null) : loadDiag())}>
            <Icon name="flask" size={14} /> {t("events.diagnostics")}
          </Button>
        </div>
      </header>

      {diag != null && (
        <Card className="raised">
          <div className="card-head">
            <h2>{t("events.diagnostics")}</h2>
            <Button small variant="ghost" onClick={() => setRaw(!raw)}>
              {raw ? t("common.close") : "JSON"}
            </Button>
          </div>

          <div className="row wrap" style={{ gap: "var(--s6)" }}>
            <DiagValue label={t("dash.wasted")} value={`${Math.round(diag.waste_w ?? 0)} W`}
                       tone={(diag.waste_w ?? 0) > 100 ? "warn" : undefined} />
            <DiagValue label={t("dash.gapFilled")} value={`${Math.round(diag.gap_filled_w ?? 0)} W`} />
            <DiagValue label={t("settings.deadband")}
                       value={`${Math.round(diag.deadband_effective_w ?? 0)} W`} />
            <DiagValue label={t("dash.grid")} value={`${Math.round(diag.grid_smoothed ?? 0)} W`} />
            {diag.weather && <DiagValue label="Wetter" value={diag.weather} />}
          </div>

          {diag.waste_reason && (
            <p className="dim" style={{ marginBottom: 0 }}>→ {diag.waste_reason}</p>
          )}

          <div className="table-scroll" style={{ marginTop: "var(--s4)" }}>
            <table className="table">
              <thead>
                <tr><th>{t("device.name")}</th><th>Status</th><th>{t("events.message")}</th></tr>
              </thead>
              <tbody>
                {(diag.devices ?? []).map((d) => (
                  <tr key={d.id}>
                    <td>{d.name}</td>
                    <td>
                      <Badge tone={d.online ? "ok" : "err"}>
                        {d.online ? t("common.online") : t("common.offline")}
                      </Badge>
                    </td>
                    <td>
                      {d.command_error
                        ? <span style={{ color: "var(--err)" }}>{d.command_error}</span>
                        : d.last_error
                          ? <span style={{ color: "var(--err)" }}>{d.last_error}</span>
                          : String(d.decision?.reason ?? "–")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {raw && (
            <pre className="panel" style={{
              overflow: "auto", maxHeight: 420, fontSize: 12, lineHeight: 1.55, marginTop: "var(--s4)",
            }}>
              {JSON.stringify(diag, null, 2)}
            </pre>
          )}
        </Card>
      )}

      <Card>
        {events.length === 0 ? (
          <Empty icon={<Icon name="events" size={30} />} title={t("events.title")} />
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("events.time")}</th>
                  <th>{t("events.level")}</th>
                  <th>{t("events.category")}</th>
                  <th>{t("events.message")}</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{fmtTime(e.time)}</td>
                    <td><Badge tone={tone(e.level)}>{e.level}</Badge></td>
                    <td>{e.category}</td>
                    <td>{e.message}</td>
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
