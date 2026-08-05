import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { TaskYieldMetric, TaskYieldPoint } from "./types";

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

const DELTA_KEYS = {
  experience: "experienceDelta",
  gold: "goldDelta",
  silver: "silverDelta",
} as const;

export function validTaskYieldPoints(points: TaskYieldPoint[], metric: TaskYieldMetric) {
  const deltaKey = DELTA_KEYS[metric];
  return points
    .filter((point) => point.validMetrics.includes(metric) && point[deltaKey] !== null)
    .map((point) => ({
      runId: point.runId,
      completedAt: point.completedAt,
      value: point[deltaKey] as number,
    }));
}

export function TaskYieldChart({
  points,
  metric,
  average,
}: {
  points: TaskYieldPoint[];
  metric: TaskYieldMetric;
  average: number | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const valid = validTaskYieldPoints(points, metric);
    const colors = {
      experience: "#71c99a",
      gold: "#e9b949",
      silver: "#83a9c7",
    };
    chart.setOption({
      animationDuration: 450,
      grid: { left: 18, right: 20, top: 28, bottom: 22, containLabel: true },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#151b27",
        borderColor: "#394257",
        textStyle: { color: "#f6f1e6" },
        valueFormatter: (value: number) => `${value > 0 ? "+" : ""}${value.toLocaleString("zh-CN")}`,
      },
      xAxis: {
        type: "category",
        data: valid.map((point) => point.completedAt),
        axisLine: { lineStyle: { color: "#394257" } },
        axisLabel: {
          color: "#7f8a9d",
          formatter: (value: string) => new Date(value).toLocaleDateString("zh-CN", {
            month: "numeric", day: "numeric",
          }),
        },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: "rgba(255,255,255,.06)" } },
        axisLabel: {
          color: "#7f8a9d",
          formatter: (value: number) => Intl.NumberFormat("zh-CN", { notation: "compact" }).format(value),
        },
      },
      series: [
        {
          name: "单次净变化",
          type: "bar",
          data: valid.map((point) => point.value),
          barMaxWidth: 34,
          itemStyle: { color: colors[metric] },
        },
        {
          name: "期间平均",
          type: "line",
          data: valid.map(() => average),
          symbol: "none",
          lineStyle: { color: "#dce3ec", width: 1, type: "dashed" },
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [points, metric, average]);
  return <div className="task-yield-chart" ref={ref} />;
}
