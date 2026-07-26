import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Metrics } from "./types";

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

export function TrendChart({ data }: { data?: Metrics }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const points = data?.points || [];
    chart.setOption({
      animationDuration: 500,
      grid: { left: 18, right: 20, top: 22, bottom: 22, containLabel: true },
      tooltip: { trigger: "axis", backgroundColor: "#151b27", borderColor: "#394257", textStyle: { color: "#f6f1e6" } },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: points.map((item) => new Date(item.capturedAt).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })),
        axisLine: { lineStyle: { color: "#394257" } },
        axisLabel: { color: "#7f8a9d" }
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: "rgba(255,255,255,.06)" } },
        axisLabel: { color: "#7f8a9d", formatter: (value: number) => Intl.NumberFormat("zh-CN", { notation: "compact" }).format(value) }
      },
      series: [{
        type: "line",
        smooth: true,
        connectNulls: false,
        showSymbol: points.length < 12,
        symbolSize: 7,
        data: points.map((item) => item.status === "success" ? item.value : null),
        lineStyle: { color: "#e9b949", width: 3 },
        itemStyle: { color: "#f6cd68", borderColor: "#080b12", borderWidth: 2 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "rgba(233,185,73,.3)" },
          { offset: 1, color: "rgba(233,185,73,0)" }
        ]) }
      }]
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.dispose(); };
  }, [data]);
  return <div className="trend-chart" ref={ref} />;
}
