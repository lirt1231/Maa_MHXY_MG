import { useEffect, useMemo, useState } from "react";
import { TaskYieldChart, validTaskYieldPoints } from "./TaskYieldChart";
import type {
  Task,
  TaskYieldMetric,
  TaskYieldMetricStats,
  TaskYieldStatistics,
  TaskYieldTaskStatistics,
} from "./types";

const METRICS: { key: TaskYieldMetric; label: string }[] = [
  { key: "experience", label: "经验" },
  { key: "gold", label: "金币" },
  { key: "silver", label: "银币" },
];

const EMPTY_METRIC: TaskYieldMetricStats = {
  average: null,
  total: null,
  sampleCount: 0,
  minimum: null,
  maximum: null,
};

function formatSigned(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  const formatted = Math.abs(value).toLocaleString("zh-CN");
  return value > 0 ? `+${formatted}` : value < 0 ? `-${formatted}` : "0";
}

function metricClass(value: number | null | undefined) {
  if (value === null || value === undefined || value === 0) return "";
  return value > 0 ? "positive" : "negative";
}

function emptyTaskStats(task: Task): TaskYieldTaskStatistics {
  return {
    taskId: task.id,
    taskName: task.name,
    attempts: 0,
    completedRuns: 0,
    reliableRuns: 0,
    coverageRate: 0,
    latestAt: null,
    metrics: {
      experience: EMPTY_METRIC,
      gold: EMPTY_METRIC,
      silver: EMPTY_METRIC,
    },
    points: [],
  };
}

export function taskYieldRows(
  tasks: Task[],
  data: TaskYieldStatistics | undefined,
  metric: TaskYieldMetric,
) {
  const statistics = new Map((data?.tasks || []).map((task) => [task.taskId, task]));
  const knownIds = new Set(tasks.map((task) => task.id));
  const rows = [
    ...tasks.map((task) => statistics.get(task.id) || emptyTaskStats(task)),
    ...(data?.tasks || []).filter((task) => !knownIds.has(task.taskId)),
  ];
  return rows.sort((left, right) => {
    const leftAverage = left.metrics[metric]?.average;
    const rightAverage = right.metrics[metric]?.average;
    if (leftAverage === null || leftAverage === undefined) return rightAverage == null ? left.taskName.localeCompare(right.taskName, "zh-CN") : 1;
    if (rightAverage === null || rightAverage === undefined) return -1;
    return rightAverage - leftAverage || left.taskName.localeCompare(right.taskName, "zh-CN");
  });
}

export function TaskYieldPanel({
  data,
  tasks,
  rangeDays,
  onRangeDaysChange,
}: {
  data?: TaskYieldStatistics;
  tasks: Task[];
  rangeDays: 7 | 30 | 90;
  onRangeDaysChange: (days: 7 | 30 | 90) => void;
}) {
  const [mode, setMode] = useState<"comparison" | "detail">("comparison");
  const [metric, setMetric] = useState<TaskYieldMetric>("silver");
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const rows = useMemo(() => taskYieldRows(tasks, data, metric), [tasks, data, metric]);
  useEffect(() => {
    if (rows.some((task) => task.taskId === selectedTaskId)) return;
    setSelectedTaskId(rows.find((task) => task.completedRuns > 0)?.taskId || rows[0]?.taskId || "");
  }, [rows, selectedTaskId]);
  const selected = rows.find((task) => task.taskId === selectedTaskId);
  const selectedMetric = selected?.metrics[metric] || EMPTY_METRIC;
  const selectedPoints = selected ? validTaskYieldPoints(selected.points, metric) : [];

  const openDetail = (taskId: string) => {
    setSelectedTaskId(taskId);
    setMode("detail");
  };

  return <div role="tabpanel" id="data-panel-task-yields" aria-labelledby="data-tab-task-yields" className="task-yield-panel">
    <section className="task-yield-toolbar" aria-label="任务收益筛选">
      <div className="segmented" aria-label="统计视图">
        <button className={mode === "comparison" ? "active" : ""} onClick={() => setMode("comparison")}>任务对比</button>
        <button className={mode === "detail" ? "active" : ""} onClick={() => setMode("detail")}>单项趋势</button>
      </div>
      <div className="segmented" aria-label="收益类型">
        {METRICS.map((item) => <button key={item.key} className={metric === item.key ? "active" : ""} onClick={() => setMetric(item.key)}>{item.label}</button>)}
      </div>
      <div className="segmented" aria-label="任务收益统计范围">
        {([7, 30, 90] as const).map((days) => <button key={days} className={rangeDays === days ? "active" : ""} onClick={() => onRangeDaysChange(days)}>近 {days} 天</button>)}
      </div>
    </section>

    {mode === "comparison" ? <section className="panel task-yield-comparison">
      <div className="panel-heading">
        <div><span className="eyebrow">近 {rangeDays} 天</span><h2>每次完整完成的平均收益</h2></div>
        <span className="panel-meta">按{METRICS.find((item) => item.key === metric)?.label}排序</span>
      </div>
      <div className="task-yield-table-wrap"><table className="task-yield-table">
        <thead><tr><th>任务</th>{METRICS.map((item) => <th key={item.key}>平均{item.label}</th>)}<th>有效记录</th><th>完整率</th></tr></thead>
        <tbody>{rows.map((task) => <tr key={task.taskId}>
          <td><button onClick={() => openDetail(task.taskId)}>{task.taskName}<span>查看趋势</span></button></td>
          {METRICS.map((item) => <td key={item.key} className={metricClass(task.metrics[item.key]?.average)}>{formatSigned(task.metrics[item.key]?.average)}</td>)}
          <td>{task.metrics[metric]?.sampleCount || 0}{(task.metrics[metric]?.sampleCount || 0) > 0 && (task.metrics[metric]?.sampleCount || 0) < 3 ? <small>样本较少</small> : null}</td>
          <td>{task.completedRuns ? `${task.reliableRuns}/${task.completedRuns}` : "—"}</td>
        </tr>)}</tbody>
      </table></div>
      {!data?.tasks.length && <div className="task-yield-empty">完成任务并成功记录前后资源后，这里会显示平均收益。</div>}
    </section> : <>
      <section className="task-yield-detail-heading">
        <label>查看任务<select value={selectedTaskId} onChange={(event) => setSelectedTaskId(event.target.value)}>{rows.map((task) => <option key={task.taskId} value={task.taskId}>{task.taskName}</option>)}</select></label>
        <small>平均值只使用该资源可可靠归因的完成记录。</small>
      </section>
      <section className="metric-grid task-yield-summary">
        <article className="metric-card featured"><span>平均每次完成</span><strong className={metricClass(selectedMetric.average)}>{formatSigned(selectedMetric.average)}</strong><small>{selectedMetric.sampleCount < 3 ? "样本较少，暂不用于任务排名" : `基于 ${selectedMetric.sampleCount} 次有效记录`}</small></article>
        <article className="metric-card"><span>期间总收益</span><strong className={metricClass(selectedMetric.total)}>{formatSigned(selectedMetric.total)}</strong><small>近 {rangeDays} 天有效记录之和</small></article>
        <article className="metric-card"><span>有效样本</span><strong>{selectedMetric.sampleCount}</strong><small>{selected?.completedRuns || 0} 次明确完成</small></article>
        <article className="metric-card"><span>完整记录率</span><strong>{selected?.completedRuns ? `${selected.coverageRate}%` : "—"}</strong><small>{selected?.reliableRuns || 0} / {selected?.completedRuns || 0} 次全部资源完整</small></article>
      </section>
      <section className="panel task-yield-chart-panel">
        <div className="panel-heading"><div><span className="eyebrow">{selected?.taskName || "任务"}</span><h2>逐次净变化</h2></div><span className="panel-meta">虚线为期间平均</span></div>
        {selectedPoints.length ? <TaskYieldChart points={selected?.points || []} metric={metric} average={selectedMetric.average} /> : <div className="task-yield-empty">这段时间还没有可用于{METRICS.find((item) => item.key === metric)?.label}趋势的完整记录。</div>}
      </section>
    </>}
  </div>;
}
