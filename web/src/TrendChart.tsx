import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Metrics } from "./types";

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

export function successfulTrendPoints(points: Metrics["points"]) {
  return points.filter((item) => item.status === "success" && item.value !== null);
}

export function trendLineDataOptions(points: Metrics["points"]) {
  return {
    connectNulls: true,
    data: successfulTrendPoints(points).map((item) => [item.capturedAt, item.value]),
  };
}

export function trendWindowBounds(rangeDays: number, now = Date.now()) {
  return {
    min: now - rangeDays * 24 * 60 * 60 * 1000,
    max: now,
  };
}

export function TrendChart({ data }: { data?: Metrics }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const points = successfulTrendPoints(data?.points || []);
    const windowBounds = trendWindowBounds(data?.rangeDays || 7);
    const experience = data?.metric === "experience";
    const colors = experience
      ? { line: "#71c99a", area: "rgba(113,201,154,.28)", edge: "#a3e2bf" }
      : { line: "#e9b949", area: "rgba(233,185,73,.3)", edge: "#f6cd68" };
    chart.setOption({
      animationDuration: 500,
      grid: { left: 18, right: 20, top: 22, bottom: 22, containLabel: true },
      tooltip: { trigger: "axis", backgroundColor: "#151b27", borderColor: "#394257", textStyle: { color: "#f6f1e6" } },
      xAxis: {
        type: "time",
        min: windowBounds.min,
        max: windowBounds.max,
        axisLine: { lineStyle: { color: "#394257" } },
        axisLabel: {
          color: "#7f8a9d",
          formatter: (value: number) => new Date(value).toLocaleDateString("zh-CN", {
            month: "numeric", day: "numeric"
          })
        }
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: "rgba(255,255,255,.06)" } },
        axisLabel: { color: "#7f8a9d", formatter: (value: number) => Intl.NumberFormat("zh-CN", { notation: "compact" }).format(value) }
      },
      series: [{
        name: experience ? "经验" : "金币",
        type: "line",
        smooth: true,
        ...trendLineDataOptions(points),
        showSymbol: points.length < 12,
        symbolSize: 7,
        lineStyle: { color: colors.line, width: 3 },
        itemStyle: { color: colors.edge, borderColor: "#080b12", borderWidth: 2 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: colors.area },
          { offset: 1, color: experience ? "rgba(113,201,154,0)" : "rgba(233,185,73,0)" }
        ]) }
      }]
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.dispose(); };
  }, [data]);
  return <div className="trend-chart" ref={ref} />;
}
