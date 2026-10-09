/** Historische Charts (ECharts): Leistungsverlauf, SoC, Energiebilanz,
 *  verschenkte Energie.
 *
 *  Die Charts folgen demselben Farbcode wie das Dashboard – PV grün, Netzbezug
 *  rot, Einspeisung blau, Batterie gelb – damit man zwischen Live-Ansicht und
 *  Verlauf nicht umdenken muss. Sie reagieren außerdem auf einen Themenwechsel,
 *  statt bis zum nächsten Datenabruf in den alten Farben stehen zu bleiben.
 */
import * as echarts from "echarts";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { get } from "../lib/api";
import { cssVar } from "../lib/format";

function useThemeVersion(): number {
  // Beobachtet das data-theme-Attribut, damit die Charts beim Umschalten
  // zwischen Hell und Dunkel sofort neu eingefärbt werden.
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const observer = new MutationObserver(() => setVersion((v) => v + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  return version;
}

/** Diagramm-Hülle. Ohne `option` zeigt sie statt einer leeren Fläche, ob
 *  noch geladen wird oder ob es (noch) keine Daten gibt. */
function EChart({ option, height, loading = false }: { option: echarts.EChartsOption | null; height: number; loading?: boolean }) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chartRef.current = echarts.init(ref.current);
    const onResize = () => chartRef.current?.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (option && chartRef.current) chartRef.current.setOption(option, { notMerge: true });
  }, [option]);

  return (
    <div className="chart-wrap" style={{ height }}>
      <div ref={ref} style={{ height, width: "100%", visibility: option ? "visible" : "hidden" }} />
      {!option && (
        loading ? <div className="chart-empty skeleton" aria-busy="true" />
          : <div className="chart-empty"><span>{t("common.noData")}</span></div>
      )}
    </div>
  );
}

type SeriesData = Record<string, { time: string; value: number }[]>;

function baseOption(): Partial<echarts.EChartsOption> {
  const dim = cssVar("--text-dim");
  const line = cssVar("--line");
  return {
    backgroundColor: "transparent",
    textStyle: { color: dim, fontFamily: "inherit", fontSize: 11.5 },
    grid: { left: 52, right: 16, top: 24, bottom: 42 },
    tooltip: {
      trigger: "axis",
      backgroundColor: cssVar("--surface"),
      borderColor: line,
      textStyle: { color: cssVar("--text-strong") },
    },
    legend: { textStyle: { color: dim }, bottom: 0, icon: "roundRect", itemHeight: 8, itemWidth: 12 },
  };
}

function timeAxis() {
  return { type: "time" as const, axisLine: { lineStyle: { color: cssVar("--line") } }, axisTick: { show: false } };
}

function valueAxis(name: string) {
  return {
    type: "value" as const,
    name,
    nameTextStyle: { color: cssVar("--text-dim") },
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: cssVar("--line") } },
  };
}

/** Daten zyklisch laden. `undefined` = lädt noch, `null` = nicht verfügbar. */
function usePolledData<T>(url: string, intervalMs = 60000): T | null | undefined {
  const [data, setData] = useState<T | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    const load = () => get<T>(url)
      .then((d) => alive && setData(d))
      .catch(() => alive && setData((prev) => (prev === undefined ? null : prev)));
    load();
    const timer = window.setInterval(load, intervalMs);
    return () => { alive = false; window.clearInterval(timer); };
  }, [url, intervalMs]);
  return data;
}

export function PowerHistoryChart({ range }: { range: string }) {
  const data = usePolledData<SeriesData>(
    `/api/history?fields=pv_power,house_power,grid_power,battery_power&range=${range}`,
  );
  const theme = useThemeVersion();
  let option: echarts.EChartsOption | null = null;
  if (data && Object.keys(data).length) {
    const mk = (field: string) => (data[field] || []).map((p) => [p.time, p.value]);
    option = {
      ...baseOption(),
      xAxis: timeAxis(),
      yAxis: valueAxis("W"),
      series: [
        { name: "PV", type: "line", showSymbol: false, smooth: true, areaStyle: { opacity: 0.18 },
          lineStyle: { width: 2 }, color: cssVar("--c-pv"), data: mk("pv_power") },
        { name: "Haus", type: "line", showSymbol: false, smooth: true,
          lineStyle: { width: 1.6 }, color: cssVar("--c-house"), data: mk("house_power") },
        { name: "Netz", type: "line", showSymbol: false, smooth: true,
          lineStyle: { width: 1.6 }, color: cssVar("--c-grid-import"), data: mk("grid_power") },
        { name: "Batterie", type: "line", showSymbol: false, smooth: true,
          lineStyle: { width: 1.6 }, color: cssVar("--c-battery"), data: mk("battery_power") },
      ],
    };
  }
  return <EChart key={theme} option={option} height={300} loading={data === undefined} />;
}

/** Verschenkter Solarstrom über die Zeit – macht das Regelziel überprüfbar. */
export function WasteChart({ range }: { range: string }) {
  const data = usePolledData<SeriesData>(`/api/history?fields=waste_power,surplus&range=${range}`);
  const theme = useThemeVersion();
  let option: echarts.EChartsOption | null = null;
  if (data && Object.keys(data).length) {
    const mk = (field: string) => (data[field] || []).map((p) => [p.time, p.value]);
    option = {
      ...baseOption(),
      xAxis: timeAxis(),
      yAxis: valueAxis("W"),
      series: [
        { name: "Verschenkt", type: "line", showSymbol: false, smooth: true,
          areaStyle: { opacity: 0.3 }, lineStyle: { width: 1.8 },
          color: cssVar("--c-waste"), data: mk("waste_power") },
        { name: "Überschuss", type: "line", showSymbol: false, smooth: true,
          lineStyle: { width: 1.4, type: "dashed" }, color: cssVar("--c-pv"), data: mk("surplus") },
      ],
    };
  }
  return <EChart key={theme} option={option} height={260} loading={data === undefined} />;
}

export function SocChart({ range }: { range: string }) {
  const data = usePolledData<SeriesData>(`/api/history?fields=battery_soc&range=${range}`);
  const theme = useThemeVersion();
  let option: echarts.EChartsOption | null = null;
  if (data && Object.keys(data).length) {
    option = {
      ...baseOption(),
      xAxis: timeAxis(),
      yAxis: { ...valueAxis("%"), min: 0, max: 100 },
      series: [
        { name: "Batterie-SoC", type: "line", showSymbol: false, smooth: true, color: cssVar("--c-battery"),
          areaStyle: { opacity: 0.2 }, data: (data.battery_soc || []).map((p) => [p.time, p.value]) },
      ],
    };
  }
  return <EChart key={theme} option={option} height={220} loading={data === undefined} />;
}

export function EnergyBalanceChart({ range }: { range: string }) {
  const data = usePolledData<Record<string, Record<string, number>>>(
    `/api/history/energy?range=${range}`, 300000,
  );
  const theme = useThemeVersion();
  let option: echarts.EChartsOption | null = null;
  if (data && Object.keys(data).length) {
    const days = Object.keys(data).sort();
    const short = days.map((d) => d.slice(5));
    option = {
      ...baseOption(),
      xAxis: {
        type: "category", data: short,
        axisLine: { lineStyle: { color: cssVar("--line") } }, axisTick: { show: false },
      },
      yAxis: valueAxis("kWh"),
      series: [
        { name: "PV", type: "bar", color: cssVar("--c-pv"), itemStyle: { borderRadius: [3, 3, 0, 0] },
          data: days.map((x) => +(data[x]?.pv_power ?? 0).toFixed(1)) },
        { name: "Bezug", type: "bar", color: cssVar("--c-grid-import"), itemStyle: { borderRadius: [3, 3, 0, 0] },
          data: days.map((x) => +(data[x]?.grid_import ?? 0).toFixed(1)) },
        { name: "Einspeisung", type: "bar", color: cssVar("--c-grid-export"), itemStyle: { borderRadius: [3, 3, 0, 0] },
          data: days.map((x) => +(data[x]?.grid_export ?? 0).toFixed(1)) },
      ],
    };
  }
  return <EChart key={theme} option={option} height={260} loading={data === undefined} />;
}

/** Kommende Strompreise als Balken; grün, was unter der Grenze liegt. */
export function PriceCurve({ prices, limit, height = 220 }: {
  prices: { start: string; minutes: number; price_ct: number }[]; limit: number | null; height?: number;
}) {
  const theme = useThemeVersion();
  let option: echarts.EChartsOption | null = null;
  if (prices.length) {
    const labels = prices.map((p) => {
      const d = new Date(p.start);
      return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    });
    const cheap = cssVar("--c-pv");
    const normal = cssVar("--line-strong");
    const base = baseOption();
    option = {
      ...base,
      legend: undefined,
      grid: { left: 40, right: 12, top: 18, bottom: 28 },
      tooltip: {
        ...(base.tooltip as object),
        trigger: "axis",
        valueFormatter: (v) => `${Number(v).toFixed(1).replace(".", ",")} ct`,
      },
      xAxis: {
        type: "category", data: labels,
        axisLine: { lineStyle: { color: cssVar("--line") } }, axisTick: { show: false },
        axisLabel: { interval: (i: number) => labels[i].endsWith(":00") && new Date(prices[i].start).getHours() % 3 === 0 },
      },
      yAxis: { ...valueAxis("ct"), min: (v: { min: number }) => Math.min(0, Math.floor(v.min)) },
      series: [{
        type: "bar",
        barCategoryGap: "18%",
        data: prices.map((p, i) => ({
          value: +p.price_ct.toFixed(2),
          itemStyle: {
            color: limit != null && p.price_ct <= limit ? cheap : normal,
            borderRadius: [3, 3, 0, 0],
            borderColor: i === 0 ? cssVar("--text-strong") : undefined,
            borderWidth: i === 0 ? 1.5 : 0,
          },
        })),
        markLine: limit == null ? undefined : {
          symbol: "none", silent: true,
          lineStyle: { color: cssVar("--c-pv"), type: "dashed", width: 1.5 },
          label: { formatter: `${limit.toFixed(1).replace(".", ",")} ct`, color: cssVar("--text-dim"), position: "insideEndTop" },
          data: [{ yAxis: limit }],
        },
      }],
    };
  }
  return <EChart key={theme} option={option} height={height} />;
}

/** Heizleistung (Fläche) und Wassertemperatur (Linie) der letzten 24 h. */
export function WaterDayChart({ deviceId }: { deviceId: number }) {
  const data = usePolledData<SeriesData>(
    `/api/history?fields=water_power,water_temp&range=24h&source=device:${deviceId}`, 120000,
  );
  const theme = useThemeVersion();
  let option: echarts.EChartsOption | null = null;
  if (data && Object.keys(data).length) {
    const mk = (field: string) => (data[field] || []).map((p) => [p.time, p.value]);
    option = {
      ...baseOption(),
      grid: { left: 46, right: 40, top: 20, bottom: 40 },
      xAxis: timeAxis(),
      yAxis: [
        valueAxis("W"),
        { ...valueAxis("°C"), splitLine: { show: false }, min: 10, max: 80 },
      ],
      series: [
        { name: "Leistung", type: "line", showSymbol: false, smooth: true, step: false,
          areaStyle: { opacity: 0.25 }, lineStyle: { width: 1.2 },
          color: cssVar("--c-water"), data: mk("water_power") },
        { name: "Temperatur", type: "line", showSymbol: false, smooth: true, yAxisIndex: 1,
          lineStyle: { width: 2 }, color: cssVar("--water-hot"), data: mk("water_temp") },
      ],
    };
  }
  return <EChart key={theme} option={option} height={220} loading={data === undefined} />;
}
