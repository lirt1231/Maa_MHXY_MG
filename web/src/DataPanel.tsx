import { TrendChart } from "./TrendChart";
import type { Metrics, SystemState } from "./types";

function formatNumber(value: number | null | undefined) {
  return value == null ? "—" : new Intl.NumberFormat("zh-CN").format(value);
}

type Props = {
  system?: SystemState; metrics?: Metrics; metricKind: "gold" | "experience";
  busy: boolean; capturing: boolean;
  onMetricKindChange: (kind: "gold" | "experience") => void;
  onCapture: () => void; onCorrect: () => void;
};

export function DataPanel({ system, metrics, metricKind, busy, capturing, onMetricKindChange, onCapture, onCorrect }: Props) {
  const latest = metrics?.snapshots.at(-1);
  return <div className="panel-page data-panel-page">
    <section className="data-hero">
      <div><span className="eyebrow">RESOURCE ANALYTICS · 7 DAYS</span><h1>数据面板</h1><p>独立查看金币与经验快照。采集过程会自动操作游戏页面并在完成后恢复主界面。</p></div>
      <div className={`snapshot-badge ${system?.metricCollectedToday ? "done" : "pending"}`}><span>{system?.metricCollectedToday ? "今日已采集" : "今日待采集"}</span><small>{latest ? new Date(latest.capturedAt).toLocaleString("zh-CN") : "暂无快照"}</small></div>
    </section>

    <section className="metric-grid data-metrics">
      <article className="metric-card featured"><span>当前{metricKind === "gold" ? "金币" : "经验"}</span><strong>{formatNumber(metrics?.current)}</strong><small>{latest ? `采集于 ${new Date(latest.capturedAt).toLocaleString("zh-CN")}` : "尚无有效采集"}</small></article>
      <article className="metric-card"><span>近 7 天变化</span><strong className={(metrics?.change || 0) >= 0 ? "positive" : "negative"}>{metrics?.change == null ? "—" : `${metrics.change >= 0 ? "+" : ""}${formatNumber(metrics.change)}`}</strong><small>按有效快照计算</small></article>
      <article className="metric-card"><span>角色等级</span><strong>{formatNumber(latest?.level)}</strong><small>{latest?.experienceRequired ? `升级需要 ${formatNumber(latest.experienceRequired)}` : "等待属性页数据"}</small></article>
      <article className="metric-card"><span>快照数量</span><strong>{metrics?.snapshots.length ?? 0}</strong><small>当前 7 天范围</small></article>
    </section>

    <section className="dashboard-row data-content">
      <article className="panel chart-panel">
        <div className="panel-heading"><div><span className="eyebrow">7 DAY SIGNAL</span><h2>资源趋势</h2></div><div className="segmented"><button className={metricKind === "gold" ? "active" : ""} onClick={() => onMetricKindChange("gold")}>金币</button><button className={metricKind === "experience" ? "active" : ""} onClick={() => onMetricKindChange("experience")}>经验</button></div></div>
        <TrendChart data={metrics} />
      </article>
      <article className="panel capture-panel">
        <span className="eyebrow">AUTOMATED SNAPSHOT</span><h2>全自动指标采集</h2><p>在游戏主界面点击采集即可。控制台会自动打开背包读取金币，再打开人物属性读取等级和经验，完成后恢复主界面。</p>
        <div className="capture-state"><span>最近状态</span><strong className={latest?.status === "success" ? "positive" : "negative"}>{latest ? latest.status === "success" ? "采集成功" : "需要处理" : "尚未采集"}</strong></div>
        {latest?.error && <small className="capture-error">{latest.error}</small>}
        <button className="button primary wide" disabled={busy || capturing || !system?.windowFound} onClick={onCapture}>{capturing ? "正在自动采集…" : "立即自动采集"}</button>
        {latest && <button className="button ghost wide correction-button" disabled={busy} onClick={onCorrect}>修正最近记录</button>}
      </article>
    </section>
  </div>;
}
