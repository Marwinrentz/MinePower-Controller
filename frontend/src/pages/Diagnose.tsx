/** Diagnose – Befunde statt Rohdaten.
 *
 *  Zuerst, was auffällt: jeder Befund mit Ursache und konkretem Schritt
 *  (berechnet in backend/app/services/diagnostics.py). Dann der Live-Zustand
 *  der Geräte samt den letzten Befehlen an die Batterie, die Regelgüte
 *  (Totband, Wetter, Verschenkt) und das Ereignisprotokoll mit Filtern.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Icon, type IconName } from "../components/icons";
import {
  ActionButton, Badge, Card, CardHead, Empty, PageHead, Segment, StatusDot,
} from "../components/ui";
import { useI18n } from "../i18n";
import { get } from "../lib/api";
import { fmtAgo, fmtClock, fmtDay, fmtNum, fmtW } from "../lib/format";
import type { AppEvent, Finding } from "../lib/types";
import { useAuth } from "../store/auth";
import { toast } from "../store/toast";

type Tab = "findings" | "devices" | "regulation" | "events";
type Range = "24h" | "7d" | "30d";

type DiagDevice = {
  id: number; name: string; driver_id: string; category: string; online: boolean; failures: number;
  last_error: string | null; command_error: string | null; last_seen: string | null;
  decision: Record<string, unknown>; sent: Record<string, unknown>;
};
type Write = { time: string; register: number; value: number; readback: number | null; ok: boolean; error: string | null; ms?: number };
type Diagnostics = {
  devices: DiagDevice[];
  regulation_config: Record<string, number | boolean | string>;
  deadband_effective_w: number;
  volatility_index: number;
  weather: string;
  grid_smoothed: number;
  waste_w: number | null;
  waste_reason: string | null;
  gap_filled_w: number | null;
  battery_control?: { devices: { id: number; name: string; writes: Write[] }[] };
};

const LEVEL_ICON: Record<Finding["level"], IconName> = { error: "warning", warn: "warning", info: "info", ok: "check" };

async function download(url: string, filename: string) {
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

/* ------------------------------------------------------------ Befunde */

function Findings() {
  const { t } = useI18n();
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  const [range, setRange] = useState<Range>("7d");
  const [data, setData] = useState<Finding[] | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    get<{ findings: Finding[] }>(`/api/system/findings?range=${range}`)
      .then((r) => alive && setData(r.findings))
      .catch((e) => { if (alive) { setData([]); toast.err(String(e.message ?? e)); } });
    return () => { alive = false; };
  }, [range]);

  const report = async () => {
    try {
      await download(`/api/system/diagnostic-report?range=${range}`, `minepower_diagnose_${range}.json`);
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  };

  const problems = (data ?? []).filter((f) => f.level !== "ok");
  const fine = (data ?? []).filter((f) => f.level === "ok");

  return (
    <>
      <div className="toolbar">
        <Segment<Range> value={range} onChange={setRange}
                        options={[{ value: "24h", label: t("diag.range24h") }, { value: "7d", label: t("diag.range7d") }, { value: "30d", label: t("diag.range30d") }]} />
        {isAdmin && (
          <ActionButton variant="ghost" onClick={report} title={t("diag.reportHelp")}>
            <Icon name="download" size={16} /> {t("diag.report")}
          </ActionButton>
        )}
      </div>
      {data === null ? (
        <div className="col">
          <div className="skeleton" style={{ height: 110 }} />
          <div className="skeleton" style={{ height: 110 }} />
          <p className="dim">{t("diag.loading")}</p>
        </div>
      ) : problems.length === 0 ? (
        <Card><Empty icon={<Icon name="check" size={32} />} title={t("diag.allGood")}>{t("diag.allGoodText")}</Empty></Card>
      ) : (
        <ul className="findings">
          {problems.map((f, i) => (
            <li key={i} className={`finding level-${f.level}`}>
              <div className="finding-head">
                <span className="finding-icon"><Icon name={LEVEL_ICON[f.level]} size={20} /></span>
                <h3>{f.title}</h3>
                {f.count != null && f.count > 1 && <Badge>{t("diag.count", { n: f.count })}</Badge>}
                <Badge tone={f.level === "error" ? "err" : f.level === "warn" ? "warn" : "info"}>{t(`diag.level.${f.level}`)}</Badge>
              </div>
              <div className="finding-body">
                <p><strong>{t("diag.cause")}: </strong>{f.detail}</p>
                {f.fix && <p className="finding-fix"><strong>{t("diag.fix")}: </strong>{f.fix}</p>}
              </div>
            </li>
          ))}
        </ul>
      )}
      {fine.length > 0 && (
        <ul className="findings">
          {fine.map((f, i) => (
            <li key={`ok-${i}`} className="finding level-ok">
              <div className="finding-head">
                <span className="finding-icon"><Icon name="check" size={20} /></span>
                <h3>{f.title}</h3>
              </div>
              <div className="finding-body"><p>{f.detail}</p></div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/* ------------------------------------------------------------ Live-Daten */

function useDiagnostics(): Diagnostics | null {
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => get<Diagnostics>("/api/system/diagnostics").then((d) => alive && setDiag(d)).catch(() => {});
    load();
    const timer = window.setInterval(load, 5000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  return diag;
}

function Devices() {
  const { t } = useI18n();
  const diag = useDiagnostics();
  if (!diag) return <div className="skeleton" style={{ height: 240 }} />;
  const writes = diag.battery_control?.devices ?? [];
  return (
    <>
      <ul className="diag-devices">
        {diag.devices.map((d) => (
          <li key={d.id} className="card diag-device">
            <div className="diag-device-head">
              <StatusDot state={d.online ? "online" : d.last_error ? "error" : "offline"} />
              <strong>{d.name}</strong>
              <span className="dim">{d.driver_id}</span>
              <span className="spacer" />
              <Badge tone={d.online ? "ok" : "err"}>{d.online ? t("diag.online") : t("diag.offline")}</Badge>
            </div>
            <dl className="kv">
              <dt>{t("diag.lastSeen")}</dt><dd>{fmtAgo(d.last_seen)}</dd>
              {d.last_error && (<><dt>{t("diag.lastError")}</dt><dd className="err-text">{d.last_error}</dd></>)}
              {d.command_error && (<><dt>{t("diag.commandError")}</dt><dd className="err-text">{d.command_error}</dd></>)}
              {typeof d.decision?.reason === "string" && (<><dt>{t("diag.decision")}</dt><dd>{String(d.decision.reason)}</dd></>)}
              {Object.keys(d.sent ?? {}).length > 0 && (
                <><dt>{t("diag.sent")}</dt><dd className="num">{Object.entries(d.sent).map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}</dd></>
              )}
            </dl>
          </li>
        ))}
      </ul>
      {writes.map((b) => (
        <Card key={b.id}>
          <CardHead title={`${t("diag.writes")} – ${b.name}`} sub={t("diag.writesHelp")} />
          {b.writes.length === 0 ? <p className="dim">{t("diag.noWrites")}</p> : (
            <div className="table-scroll">
              <table className="table">
                <thead><tr><th>{t("diag.time")}</th><th>{t("diag.register")}</th><th>{t("diag.value")}</th><th>{t("diag.readback")}</th><th /></tr></thead>
                <tbody>
                  {[...b.writes].reverse().map((w, i) => (
                    <tr key={i}>
                      <td className="num">{fmtDay(w.time)} {fmtClock(w.time)}</td>
                      <td className="num">{w.register}</td>
                      <td className="num">{w.value}</td>
                      <td className="num">{w.readback ?? "–"}</td>
                      <td>{w.ok ? <Badge tone="ok">{t("diag.ok")}</Badge> : <Badge tone="err" title={w.error ?? ""}>{t("diag.failed")}</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ))}
    </>
  );
}

function Regulation() {
  const { t } = useI18n();
  const diag = useDiagnostics();
  if (!diag) return <div className="skeleton" style={{ height: 240 }} />;
  const cfg = diag.regulation_config;
  const adaptive = cfg.adaptive_deadband !== false;
  const min = Number(cfg.deadband_min_w ?? 40);
  const max = Number(cfg.deadband_max_w ?? 400);
  const eff = Number(diag.deadband_effective_w ?? cfg.deadband_w ?? 100);
  const pos = max > min ? Math.max(0, Math.min(100, ((eff - min) / (max - min)) * 100)) : 50;
  return (
    <div className="grid-auto">
      <Card>
        <CardHead title={t("diag.deadband")} />
        <p className="big-number num">±{fmtW(eff)}</p>
        {adaptive ? (
          <>
            <div className="range-meter" aria-hidden="true">
              <div className="range-meter-track" />
              <div className="range-meter-dot" style={{ left: `${pos}%` }} />
              <span className="range-meter-min num">{fmtW(min)}</span>
              <span className="range-meter-max num">{fmtW(max)}</span>
            </div>
            <p className="field-help">{t("diag.deadbandText", { band: fmtW(eff), min: fmtW(min), max: fmtW(max) })}</p>
          </>
        ) : <p className="field-help">{t("diag.deadbandFixed", { band: fmtW(eff) })}</p>}
      </Card>
      <Card>
        <CardHead title={t("diag.weather")} />
        <p className="big-number">{diag.weather}</p>
        <p className="field-help">{t("diag.weatherIndex", { value: fmtNum(diag.volatility_index, 2) })}</p>
      </Card>
      <Card>
        <CardHead title={t("diag.gridNow")} />
        <p className="big-number num">{fmtW(diag.grid_smoothed)}</p>
      </Card>
      <Card>
        <CardHead title={t("diag.wasteNow")} />
        <p className={`big-number num ${(diag.waste_w ?? 0) > 100 ? "t-waste" : ""}`}>{fmtW(diag.waste_w ?? 0)}</p>
        {diag.waste_reason && <p className="field-help">{diag.waste_reason}</p>}
      </Card>
      <Card>
        <CardHead title={t("diag.gapNow")} />
        <p className="big-number num">{fmtW(diag.gap_filled_w ?? 0)}</p>
        <p className="field-help">{t("diag.gapText")}</p>
      </Card>
      <Card>
        <CardHead title={t("diag.interval")} />
        <p className="big-number num">{String(cfg.interval_s)} s</p>
        <p className="field-help">{t("diag.intervalText", { s: String(cfg.interval_s) })}</p>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------ Ereignisse */

type Filter = "all" | "warn" | "control" | "device" | "config" | "waste";

function Events() {
  const { t } = useI18n();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(200);
  const [events, setEvents] = useState<AppEvent[] | null>(null);

  useEffect(() => {
    let alive = true;
    const params = new URLSearchParams({ limit: String(limit) });
    if (filter === "warn") params.set("level", "warning");
    else if (filter !== "all") params.set("category", filter);
    get<AppEvent[]>(`/api/events?${params}`).then((e) => alive && setEvents(e)).catch(() => alive && setEvents([]));
    return () => { alive = false; };
  }, [filter, limit]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (events ?? []).filter((e) => !q || e.message.toLowerCase().includes(q));
  }, [events, query]);

  const groups = useMemo(() => {
    const out: { day: string; items: AppEvent[] }[] = [];
    for (const e of shown) {
      const day = fmtDay(e.time);
      if (!out.length || out[out.length - 1].day !== day) out.push({ day, items: [] });
      out[out.length - 1].items.push(e);
    }
    return out;
  }, [shown]);

  return (
    <>
      <div className="toolbar">
        <Segment<Filter> value={filter} onChange={setFilter} options={[
          { value: "all", label: t("diag.filterAll") },
          { value: "warn", label: t("diag.filterWarn") },
          { value: "control", label: t("diag.filterControl") },
          { value: "device", label: t("diag.filterDevice") },
          { value: "waste", label: t("diag.filterWaste") },
          { value: "config", label: t("diag.filterConfig") },
        ]} />
        <input className="input search" type="search" placeholder={t("diag.search")} value={query}
               onChange={(e) => setQuery(e.target.value)} aria-label={t("diag.search")} />
      </div>
      {events === null ? <div className="skeleton" style={{ height: 240 }} />
        : groups.length === 0 ? <Card><Empty icon={<Icon name="events" size={28} />} title={t("diag.empty")} /></Card>
        : (
          <div className="events">
            {groups.map((g) => (
              <section key={g.day} className="event-day">
                <h3 className="event-day-title">{g.day}</h3>
                <ul>
                  {g.items.map((e) => (
                    <li key={e.id} className={`event level-${e.level}`}>
                      <span className="event-time num">{fmtClock(e.time)}</span>
                      <span className="event-dot" aria-hidden="true" />
                      <span className="event-msg">{e.message}</span>
                      <span className="event-cat">{e.category}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            {events.length >= limit && (
              <button type="button" className="btn ghost" onClick={() => setLimit((l) => l + 300)}>{t("diag.more")}</button>
            )}
          </div>
        )}
    </>
  );
}

/* ------------------------------------------------------------ Seite */

const TABS: { key: Tab; label: string; icon: IconName }[] = [
  { key: "findings", label: "diag.tabFindings", icon: "pulse" },
  { key: "devices", label: "diag.tabDevices", icon: "devices" },
  { key: "regulation", label: "diag.tabRegulation", icon: "auto" },
  { key: "events", label: "diag.tabEvents", icon: "events" },
];

export function Diagnose() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "findings";
  const setTab = (v: Tab) => setParams(v === "findings" ? {} : { tab: v }, { replace: true });
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => {
    navRef.current?.querySelector(".active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [tab]);
  return (
    <div className="page">
      <PageHead title={t("diag.title")} sub={t("diag.sub")} />
      <nav ref={navRef} className="subnav" aria-label={t("diag.title")}>
        {TABS.map((x) => (
          <button key={x.key} type="button" className={`subnav-item ${tab === x.key ? "active" : ""}`}
                  aria-current={tab === x.key ? "page" : undefined} onClick={() => setTab(x.key)}>
            <Icon name={x.icon} size={18} />
            <span>{t(x.label)}</span>
          </button>
        ))}
      </nav>
      {tab === "findings" && <Findings />}
      {tab === "devices" && <Devices />}
      {tab === "regulation" && <Regulation />}
      {tab === "events" && <Events />}
    </div>
  );
}
