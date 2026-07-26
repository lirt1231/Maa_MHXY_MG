import { useState } from "react";
import { CharacterResourcesView, DailyTasksView } from "./GameDataViews";
import { TrendChart } from "./TrendChart";
import type { GameDataResponse, GameDataSnapshot, MetricSnapshot, Metrics, SystemState } from "./types";

function formatNumber(value: number | null | undefined) {
  return value == null ? "—" : new Intl.NumberFormat("zh-CN").format(value);
}

function formatSigned(value: number | null | undefined) {
  if (value == null) return "—";
  return `${value >= 0 ? "+" : ""}${formatNumber(value)}`;
}

function formatTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  });
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
  gameData?: GameDataResponse;
  metricKind: "gold" | "experience";
  rangeDays: 7 | 30;
  busy: boolean;
  capturing: boolean;
  capturingDailyTasks: boolean;
  onMetricKindChange: (kind: "gold" | "experience") => void;
  onRangeDaysChange: (days: 7 | 30) => void;
  onCapture: () => void;
  onCaptureDailyTasks: () => void;
  onCorrect: () => void;
};

export function DataPanel({
  system, metrics, gameData, metricKind, rangeDays, busy, capturing,
  capturingDailyTasks, onMetricKindChange, onRangeDaysChange, onCapture,
  onCaptureDailyTasks, onCorrect
}: Props) {
  const [view, setView] = useState<"resources" | "daily" | "history">("resources");
  const latest = metrics?.snapshots.at(-1);
  const latestSuccessful = [...(metrics?.snapshots || [])].reverse().find((item) => item.status === "success");
  const stats = metrics?.stats;
  const history = [...(metrics?.snapshots || [])].reverse().slice(0, 8);
  const metricLabel = metricKind === "gold" ? "金币" : "经验";
  const changeClass = (value: number | null | undefined) => (value || 0) >= 0 ? "positive" : "negative";
  const latestResource = gameData?.latestByCategory.character_resources || (
    latestSuccessful ? {
      id: latestSuccessful.id,
      capturedAt: latestSuccessful.capturedAt,
      category: "character_resources",
      sourcePage: "历史指标快照",
      status: "success",
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
  const latestDaily = gameData?.latestByCategory.daily_tasks;
  const latestRaw = (gameData?.items || []).reduce<GameDataSnapshot | undefined>((current, item) => {
    if (!current) return item;
    return Date.parse(item.capturedAt) > Date.parse(current.capturedAt) ? item : current;
  }, undefined);

  return <div className="panel-page data-panel-page">
    <section className="data-hero">
      <div>
        <span className="eyebrow">GAME DATA · SOURCE SNAPSHOTS</span>
        <h1>数据面板</h1>
        <p>查看角色资源、今日任务与历史趋势。</p>
      </div>
      <div className={`snapshot-badge ${latestRaw ? "done" : "pending"}`}>
        <span>{latestRaw ? "原始数据已接入" : "等待原始数据"}</span>
        <small>{latestRaw ? formatTime(latestRaw.capturedAt) : "暂无快照"}</small>
      </div>
    </section>

    <div className="segmented data-view-tabs" role="tablist" aria-label="数据视图">
      <button id="data-tab-resources" role="tab" aria-selected={view === "resources"} aria-controls="data-panel-resources" className={view === "resources" ? "active" : ""} onClick={() => setView("resources")}>角色资源</button>
      <button id="data-tab-daily" role="tab" aria-selected={view === "daily"} aria-controls="data-panel-daily" className={view === "daily" ? "active" : ""} onClick={() => setView("daily")}>今日任务</button>
      <button id="data-tab-history" role="tab" aria-selected={view === "history"} aria-controls="data-panel-history" className={view === "history" ? "active" : ""} onClick={() => setView("history")}>历史趋势</button>
    </div>

    {view === "resources" && <CharacterResourcesView snapshot={latestResource} />}
    {view === "daily" && <DailyTasksView
      snapshot={latestDaily}
      capturing={capturingDailyTasks}
      disabled={busy || capturing || !system?.available}
      onCapture={onCaptureDailyTasks}
    />}
    {view === "history" && <div role="tabpanel" id="data-panel-history" aria-labelledby="data-tab-history">

    <section className="data-toolbar" aria-label="数据筛选">
      <div className="segmented" aria-label="指标类型">
        <button className={metricKind === "gold" ? "active" : ""} onClick={() => onMetricKindChange("gold")}>金币</button>
        <button className={metricKind === "experience" ? "active" : ""} onClick={() => onMetricKindChange("experience")}>经验</button>
      </div>
      <div className="segmented" aria-label="统计范围">
        <button className={rangeDays === 7 ? "active" : ""} onClick={() => onRangeDaysChange(7)}>近 7 天</button>
        <button className={rangeDays === 30 ? "active" : ""} onClick={() => onRangeDaysChange(30)}>近 30 天</button>
      </div>
      <button
        className="button export-button"
        disabled={!metrics?.snapshots.length}
        onClick={() => downloadSnapshots(metrics?.snapshots || [], rangeDays)}
      ><span aria-hidden="true">↓</span>导出 CSV</button>
    </section>

    <section className="metric-grid data-metrics expanded">
      <article className="metric-card featured"><span>当前{metricLabel}</span><strong>{formatNumber(metrics?.current)}</strong><small>{latestSuccessful ? `更新于 ${formatTime(latestSuccessful.capturedAt)}` : "尚无有效采集"}</small></article>
      <article className="metric-card"><span>{rangeDays} 天净变化</span><strong className={changeClass(metrics?.change)}>{formatSigned(metrics?.change)}</strong><small>首尾有效快照差值</small></article>
      <article className="metric-card"><span>最近一次变化</span><strong className={changeClass(stats?.latestChange)}>{formatSigned(stats?.latestChange)}</strong><small>相邻有效快照</small></article>
      <article className="metric-card"><span>平均每次变化</span><strong className={changeClass(stats?.averageChangePerCapture)}>{formatSigned(stats?.averageChangePerCapture)}</strong><small>按有效快照间隔</small></article>
      <article className="metric-card"><span>区间高 / 低</span><strong className="metric-pair">{formatNumber(stats?.maximum)}<i />{formatNumber(stats?.minimum)}</strong><small>有效{metricLabel}范围</small></article>
      <article className="metric-card"><span>采集成功率</span><strong>{stats ? `${stats.captureSuccessRate}%` : "—"}</strong><small>{stats ? `${stats.successfulCaptures} 成功 · ${stats.failedCaptures} 失败` : "暂无采集"}</small></article>
    </section>

    <section className="character-strip" aria-label="最近角色快照">
      <div><span>角色等级</span><strong>{formatNumber(latestSuccessful?.level)}</strong></div>
      <div><span>当前经验</span><strong>{formatNumber(latestSuccessful?.experience)}</strong></div>
      <div><span>升级所需经验</span><strong>{formatNumber(latestSuccessful?.experienceRequired)}</strong></div>
      <div><span>有效采集天数</span><strong>{stats?.daysWithData ?? 0}</strong></div>
      <div><span>人工修正</span><strong>{stats?.correctedCaptures ?? 0}</strong></div>
    </section>

    <section className="dashboard-row data-content">
      <article className="panel chart-panel">
        <div className="panel-heading"><div><span className="eyebrow">{rangeDays} DAY SIGNAL</span><h2>{metricLabel}趋势</h2></div><span className="panel-meta">均值 {formatNumber(stats?.average)}</span></div>
        <TrendChart data={metrics} />
      </article>
      <article className="panel capture-panel">
        <span className="eyebrow">AUTOMATED SNAPSHOT</span><h2>全自动指标采集</h2>
        <p>自动读取背包金币、角色等级与经验，完成后恢复主界面。</p>
        <div className="capture-state"><span>最近状态</span><strong className={latest?.status === "success" ? "positive" : "negative"}>{latest ? latest.status === "success" ? "采集成功" : "需要处理" : "尚未采集"}</strong></div>
        <div className="capture-state"><span>区间采集</span><strong>{stats?.attempts ?? 0} 次</strong></div>
        {latest?.error && <small className="capture-error">{latest.error}</small>}
        <button className="button primary wide" disabled={busy || capturing || capturingDailyTasks || !system?.available} onClick={onCapture}>{capturing ? "正在自动采集…" : "立即自动采集"}</button>
        {latest && <button className="button ghost wide correction-button" disabled={busy} onClick={onCorrect}>修正最近记录</button>}
      </article>
    </section>

    <section className="panel snapshot-history">
      <div className="panel-heading"><div><span className="eyebrow">CAPTURE HISTORY</span><h2>最近采集记录</h2></div><span className="panel-meta">显示最近 {history.length} 条</span></div>
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
      </table></div> : <div className="snapshot-empty">当前范围内暂无采集记录</div>}
    </section>
    </div>}
  </div>;
}
