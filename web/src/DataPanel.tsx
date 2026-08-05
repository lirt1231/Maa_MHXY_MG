import { useState } from "react";
import { CharacterResourcesView, DataUpdateError } from "./GameDataViews";
import { TaskYieldPanel } from "./TaskYieldPanel";
import { TrendChart } from "./TrendChart";
import type { GameDataResponse, GameDataSnapshot, MetricSnapshot, Metrics, SystemState, Task, TaskYieldStatistics } from "./types";

function formatNumber(value: number | null | undefined) {
  return value == null ? "—" : new Intl.NumberFormat("zh-CN").format(value);
}

function formatSigned(value: number | null | undefined) {
  if (value == null) return "—";
  if (value === 0) return "0";
  return `${value >= 0 ? "+" : ""}${formatNumber(value)}`;
}

function changeDescription(value: number | null | undefined, rangeDays: number) {
  if (value == null) return `近 ${rangeDays} 天还没有足够的数据`;
  if (value === 0) return `近 ${rangeDays} 天基本没有变化`;
  return `近 ${rangeDays} 天${value > 0 ? "增加" : "减少"}了 ${formatNumber(Math.abs(value))}`;
}

function latestRecordChange(metrics: Metrics | undefined, metricKind: "gold" | "experience") {
  const field = metricKind;
  const validSnapshots = (metrics?.snapshots || []).filter((snapshot) =>
    snapshot.status === "success" && snapshot[field] !== null
  );
  if (validSnapshots.length < 2) {
    return { value: null, description: "需要至少两次有效记录" };
  }
  const previous = validSnapshots[validSnapshots.length - 2];
  const latest = validSnapshots[validSnapshots.length - 1];
  if (metricKind === "experience" && (
    previous.level == null || latest.level == null || previous.level !== latest.level
  )) {
    return { value: null, description: "等级发生变化，暂不计算跨等级差值" };
  }
  const previousValue = previous[field];
  const latestValue = latest[field];
  if (previousValue == null || latestValue == null) {
    return { value: null, description: "需要至少两次有效记录" };
  }
  const value = latestValue - previousValue;
  if (value === 0) return { value, description: "与上次记录相同" };
  return {
    value,
    description: `比上次${value > 0 ? "增加" : "减少"} ${formatNumber(Math.abs(value))}`,
  };
}

function formatTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  });
}

function snapshotTime(snapshot: GameDataSnapshot) {
  const timestamp = Date.parse(snapshot.capturedAt);
  return Number.isNaN(timestamp) ? Number.NEGATIVE_INFINITY : timestamp;
}

function latestSnapshot(snapshots: GameDataSnapshot[]) {
  return snapshots.reduce<GameDataSnapshot | undefined>((latest, snapshot) => {
    if (!latest) return snapshot;
    if (snapshotTime(snapshot) !== snapshotTime(latest)) {
      return snapshotTime(snapshot) > snapshotTime(latest) ? snapshot : latest;
    }
    return snapshot.id > latest.id ? snapshot : latest;
  }, undefined);
}

function categorySnapshots(gameData: GameDataResponse | undefined, category: string) {
  const candidates = [...(gameData?.items || [])];
  const declaredSnapshots = [
    gameData?.latestByCategory?.[category],
    gameData?.latestAttemptByCategory?.[category],
    gameData?.latestUsableByCategory?.[category],
  ];
  for (const declaredSnapshot of declaredSnapshots) {
    if (declaredSnapshot && !candidates.some((item) => item.id === declaredSnapshot.id)) {
      candidates.push(declaredSnapshot);
    }
  }
  const categoryItems = candidates.filter((item) => item.category === category);
  return {
    attempt: gameData?.latestAttemptByCategory?.[category] || latestSnapshot(categoryItems),
    usable: gameData?.latestUsableByCategory?.[category]
      || latestSnapshot(categoryItems.filter((item) => item.status === "success")),
  };
}

function csvCell(value: string | number | null | undefined) {
  const text = value == null ? "" : String(value);
  return `"${text.replaceAll('"', '""')}"`;
}

export function snapshotsToCsv(snapshots: MetricSnapshot[]) {
  const header = ["采集时间", "状态", "金币", "等级", "当前经验", "升级所需经验", "人工修正", "错误原因"];
  const rows = snapshots.map((snapshot) => [
    snapshot.capturedAt,
    snapshot.status === "success" ? "成功" : "失败",
    snapshot.gold,
    snapshot.level,
    snapshot.experience,
    snapshot.experienceRequired,
    snapshot.corrected ? "是" : "否",
    snapshot.error || ""
  ]);
  return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
}

function downloadSnapshots(snapshots: MetricSnapshot[], rangeDays: number) {
  const blob = new Blob(["\ufeff", snapshotsToCsv(snapshots)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `梦手数据快照-${rangeDays}天-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function SnapshotStatus({ snapshot }: { snapshot: MetricSnapshot }) {
  if (snapshot.status !== "success") return <span className="snapshot-status failed">失败</span>;
  if (snapshot.corrected) return <span className="snapshot-status corrected">已修正</span>;
  return <span className="snapshot-status succeeded">成功</span>;
}

type Props = {
  system?: SystemState;
  metrics?: Metrics;
  taskYields?: TaskYieldStatistics;
  tasks: Task[];
  gameData?: GameDataResponse;
  metricKind: "gold" | "experience";
  rangeDays: 7 | 30;
  taskYieldRangeDays: 7 | 30 | 90;
  busy: boolean;
  capturing: boolean;
  onMetricKindChange: (kind: "gold" | "experience") => void;
  onRangeDaysChange: (days: 7 | 30) => void;
  onTaskYieldRangeDaysChange: (days: 7 | 30 | 90) => void;
  onCapture: () => void;
  onCorrect: () => void;
};

export function DataPanel({
  system, metrics, taskYields, tasks, gameData, metricKind, rangeDays,
  taskYieldRangeDays, busy, capturing,
  onMetricKindChange, onRangeDaysChange, onTaskYieldRangeDaysChange,
  onCapture, onCorrect,
}: Props) {
  const [view, setView] = useState<"resources" | "task-yields" | "asset-history">("resources");
  const activeMetrics = metrics?.metric === metricKind && metrics.rangeDays === rangeDays
    ? metrics
    : undefined;
  const latest = activeMetrics?.snapshots.at(-1);
  const latestSuccessful = [...(activeMetrics?.snapshots || [])].reverse().find((item) => item.status === "success");
  const stats = activeMetrics?.stats;
  const latestChange = latestRecordChange(activeMetrics, metricKind);
  const history = [...(activeMetrics?.snapshots || [])].reverse().slice(0, 8);
  const metricLabel = metricKind === "gold" ? "金币" : "经验";
  const changeClass = (value: number | null | undefined) => (value || 0) >= 0 ? "positive" : "negative";
  const resourceSnapshots = categorySnapshots(gameData, "character_resources");
  const latestResource = resourceSnapshots.usable || (
    latestSuccessful ? {
      id: latestSuccessful.id,
      capturedAt: latestSuccessful.capturedAt,
      gameDate: latestSuccessful.capturedAt.slice(0, 10),
      schemaVersion: 1,
      targetId: gameData?.targetId || "local-primary",
      category: "character_resources",
      sourcePage: "历史指标快照",
      status: "success",
      stale: false,
      data: {
        gold: latestSuccessful.gold,
        level: latestSuccessful.level,
        experience: latestSuccessful.experience,
        experienceRequired: latestSuccessful.experienceRequired,
      },
      evidence: { migratedFromMetrics: true },
      error: latestSuccessful.error,
    } satisfies GameDataSnapshot : undefined
  );
  const latestRaw = resourceSnapshots.attempt || latestResource;
  const rawStatus = latestRaw ? {
    success: { label: "数据已更新", className: "done" },
    partial: { label: "部分内容待确认", className: "pending" },
    failed: { label: "这次更新失败", className: "failed" },
  }[latestRaw.status] : { label: "还没有记录", className: "pending" };

  return <div className="panel-page data-panel-page">
    <section className="data-hero">
      <div>
        <span className="eyebrow">金币与经验</span>
        <h1>我的收益</h1>
        <p>看看当前资源和最近的收益变化。</p>
      </div>
      <div className={`snapshot-badge ${rawStatus.className}`}>
        <span>{rawStatus.label}</span>
        <small>{latestRaw ? formatTime(latestRaw.capturedAt) : "完成一次记录后显示"}</small>
      </div>
    </section>

    <div className="segmented data-view-tabs" role="tablist" aria-label="数据视图">
      <button id="data-tab-resources" role="tab" aria-selected={view === "resources"} aria-controls="data-panel-resources" className={view === "resources" ? "active" : ""} onClick={() => setView("resources")}>当前资源</button>
      <button id="data-tab-task-yields" role="tab" aria-selected={view === "task-yields"} aria-controls="data-panel-task-yields" className={view === "task-yields" ? "active" : ""} onClick={() => setView("task-yields")}>任务收益</button>
      <button id="data-tab-history" role="tab" aria-selected={view === "asset-history"} aria-controls="data-panel-history" className={view === "asset-history" ? "active" : ""} onClick={() => setView("asset-history")}>资产走势</button>
    </div>

    {view === "resources" && <CharacterResourcesView
      snapshot={latestResource}
      latestAttempt={resourceSnapshots.attempt}
      capturing={capturing}
      disabled={busy || !system?.available}
      onCapture={onCapture}
    />}
    {view === "task-yields" && <TaskYieldPanel
      data={taskYields}
      tasks={tasks}
      rangeDays={taskYieldRangeDays}
      onRangeDaysChange={onTaskYieldRangeDaysChange}
    />}
    {view === "asset-history" && <div role="tabpanel" id="data-panel-history" aria-labelledby="data-tab-history">

    <section className="data-toolbar" aria-label="数据筛选">
      <div className="segmented" aria-label="查看内容">
        <button className={metricKind === "gold" ? "active" : ""} onClick={() => onMetricKindChange("gold")}>金币</button>
        <button className={metricKind === "experience" ? "active" : ""} onClick={() => onMetricKindChange("experience")}>经验</button>
      </div>
      <div className="segmented" aria-label="统计范围">
        <button className={rangeDays === 7 ? "active" : ""} onClick={() => onRangeDaysChange(7)}>近 7 天</button>
        <button className={rangeDays === 30 ? "active" : ""} onClick={() => onRangeDaysChange(30)}>近 30 天</button>
      </div>
    </section>

    <section className="metric-grid data-metrics summary">
      <article className="metric-card featured"><span>当前{metricLabel}</span><strong>{formatNumber(activeMetrics?.current)}</strong><small>{latestSuccessful ? `更新于 ${formatTime(latestSuccessful.capturedAt)}` : "正在加载对应数据"}</small></article>
      <article className="metric-card"><span>相比上次记录</span><strong className={changeClass(latestChange.value)}>{formatSigned(latestChange.value)}</strong><small>{latestChange.description}</small></article>
      <article className="metric-card"><span>这段时间的变化</span><strong className={changeClass(activeMetrics?.change)}>{formatSigned(activeMetrics?.change)}</strong><small>{changeDescription(activeMetrics?.change, rangeDays)}</small></article>
    </section>

    <section className="dashboard-row data-content">
      <article className="panel chart-panel">
        <div className="panel-heading"><div><span className="eyebrow">近 {rangeDays} 天走势</span><h2>{metricLabel}变化</h2></div><span className="panel-meta">{changeDescription(activeMetrics?.change, rangeDays)}</span></div>
        <TrendChart data={activeMetrics} />
      </article>
      <article className="panel capture-panel">
        <span className="eyebrow">记录数据</span><h2>自动更新游戏数据</h2>
        <p>自动记录背包金币、角色等级与经验，并关闭记录过程中打开的数据页面。</p>
        <div className="capture-state"><span>最近状态</span><strong className={latest?.status === "success" ? "positive" : "negative"}>{latest ? latest.status === "success" ? "更新成功" : "需要处理" : "尚未更新"}</strong></div>
        <div className="capture-state"><span>最近 {rangeDays} 天</span><strong>记录 {stats?.attempts ?? 0} 次</strong></div>
        {latest?.status === "failed" ? <DataUpdateError
          title="游戏数据更新失败"
          guidance="请确认游戏窗口已经打开并且没有被其他窗口遮挡，然后再试一次。"
          technicalError={latest.error}
          retryLabel="重新更新游戏数据"
          retrying={capturing}
          disabled={busy || !system?.available}
          onRetry={onCapture}
        /> : <button className="button primary wide" disabled={busy || capturing || !system?.available} onClick={onCapture}>{capturing ? "正在自动更新…" : "立即更新数据"}</button>}
        {latest && <button className="button ghost wide correction-button" disabled={busy} onClick={onCorrect}>修正最近记录</button>}
      </article>
    </section>

    <section className="panel snapshot-history">
      <div className="panel-heading"><div><span className="eyebrow">历史记录</span><h2>最近的数据记录</h2></div><span className="panel-meta">显示最近 {history.length} 条</span></div>
      {history.length ? <div className="snapshot-table-wrap"><table className="snapshot-table">
        <thead><tr><th>时间</th><th>状态</th><th>金币</th><th>等级</th><th>当前经验</th><th>升级所需</th></tr></thead>
        <tbody>{history.map((snapshot) => <tr key={snapshot.id}>
          <td>{formatTime(snapshot.capturedAt)}</td>
          <td><SnapshotStatus snapshot={snapshot} /></td>
          <td>{formatNumber(snapshot.gold)}</td>
          <td>{formatNumber(snapshot.level)}</td>
          <td>{formatNumber(snapshot.experience)}</td>
          <td>{formatNumber(snapshot.experienceRequired)}</td>
        </tr>)}</tbody>
      </table></div> : <div className="snapshot-empty">这段时间还没有数据记录</div>}
    </section>

    <details className="data-details">
      <summary>查看详细数据</summary>
      <section className="metric-grid data-metrics expanded" aria-label="详细统计">
        <article className="metric-card"><span>平均每次变化</span><strong className={changeClass(stats?.averageChangePerCapture)}>{formatSigned(stats?.averageChangePerCapture)}</strong></article>
        <article className="metric-card"><span>最高 / 最低</span><strong className="metric-pair">{formatNumber(stats?.maximum)}<i />{formatNumber(stats?.minimum)}</strong></article>
        <article className="metric-card"><span>记录成功率</span><strong>{stats ? `${stats.captureSuccessRate}%` : "—"}</strong><small>{stats ? `${stats.successfulCaptures} 次成功 · ${stats.failedCaptures} 次失败` : "暂无记录"}</small></article>
      </section>
      <section className="character-strip" aria-label="其他详细数据">
        <div><span>平均{metricLabel}</span><strong>{formatNumber(stats?.average)}</strong></div>
        <div><span>有数据的天数</span><strong>{stats?.daysWithData ?? 0}</strong></div>
        <div><span>手动修正次数</span><strong>{stats?.correctedCaptures ?? 0}</strong></div>
      </section>
      <button
        className="button export-button"
        disabled={!activeMetrics?.snapshots.length}
        onClick={() => downloadSnapshots(activeMetrics?.snapshots || [], rangeDays)}
      ><span aria-hidden="true">↓</span>导出 CSV</button>
    </details>
    </div>}
  </div>;
}
