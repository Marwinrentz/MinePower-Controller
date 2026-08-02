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

function EChart({ option, height }: { option: echarts.EChartsOption | null; height: number }) {
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

  return <div ref={ref} style={{ height, width: "100%" }} />;
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

function usePolledData<T>(url: string, intervalMs = 60000): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => get<T>(url).then((d) => alive && setData(d)).catch(() => {});
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
  return <EChart key={theme} option={option} height={300} />;
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
  return <EChart key={theme} option={option} height={260} />;
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
  return <EChart key={theme} option={option} height={220} />;
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
  return <EChart key={theme} option={option} height={260} />;
}
