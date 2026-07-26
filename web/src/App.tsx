import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { MetricCorrectionDialog } from "./MetricCorrectionDialog";
import { TaskDialog } from "./TaskDialog";
import { TrendChart } from "./TrendChart";
import type { LiveEvent, Metrics, Run, RunSummary, SystemState, Task } from "./types";

const ACTIVE = new Set(["preparing", "running", "stopping"]);
const EMPTY_SUMMARY: RunSummary = { total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 };

function formatNumber(value: number | null | undefined) {
  return value == null ? "—" : new Intl.NumberFormat("zh-CN").format(value);
}

function duration(ms?: number | null) {
  if (!ms) return "—";
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  return minutes ? `${minutes}分 ${seconds}秒` : `${seconds}秒`;
}

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return <span className={`status-chip ${ok ? "ok" : "muted"}`}><i />{label}</span>;
}

export function App() {
  const [system, setSystem] = useState<SystemState>();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [summary, setSummary] = useState<RunSummary>(EMPTY_SUMMARY);
  const [metricKind, setMetricKind] = useState<"gold" | "experience">("gold");
  const [metrics, setMetrics] = useState<Metrics>();
  const [selected, setSelected] = useState<Task>();
  const [logs, setLogs] = useState<LiveEvent[]>([]);
  const [message, setMessage] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [startingGame, setStartingGame] = useState(false);
  const [dailyDismissed, setDailyDismissed] = useState(false);

  const refresh = useCallback(async () => {
    const [systemData, taskData, runData, metricData] = await Promise.all([
      api.system(), api.tasks(), api.runs(), api.metrics(metricKind)
    ]);
    setSystem(systemData); setTasks(taskData.items); setRuns(runData.items); setSummary(runData.summary); setMetrics(metricData);
  }, [metricKind]);

  useEffect(() => { refresh().catch((error) => setMessage(error.message)); }, [refresh]);
  useEffect(() => {
    const source = new EventSource("/api/events");
    const receive = (event: MessageEvent) => {
      const item = JSON.parse(event.data) as LiveEvent;
      setLogs((current) => [...current.slice(-199), item]);
      if (item.type === "status" || item.type === "metric") refresh().catch(() => undefined);
    };
    ["status", "log", "metric", "system"].forEach((type) => source.addEventListener(type, receive));
    source.onerror = () => setMessage("实时连接暂时中断，正在自动重连…");
    source.onopen = () => setMessage("");
    return () => source.close();
  }, [refresh]);

  const current = system?.run;
  const busy = Boolean(current && ACTIVE.has(current.status));
  const latest = metrics?.snapshots.at(-1);
  const currentValue = metrics?.current;
  const progress = useMemo(() => current?.stage || (busy ? "自动化运行中" : "等待任务"), [current, busy]);

  async function startTask(options: Record<string, unknown>) {
    if (!selected) return;
    const run = await api.start(selected.id, options);
    setSystem((state) => state ? { ...state, status: run.status, run } : state);
    setLogs([]);
  }

  async function stopTask() {
    if (!current) return;
    try { await api.stop(current.id); await refresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "停止失败"); }
  }

  async function capture() {
    setCapturing(true); setMessage("");
    try {
      const result = await api.capture() as { status?: string; error?: string };
      setMessage(result.status === "success" ? "指标采集完成" : result.error || "指标采集失败");
      await refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "指标采集失败"); }
    finally { setCapturing(false); }
  }

  async function startGame() {
    setStartingGame(true); setMessage("");
    try { await api.startGame(); setMessage("梦幻西游已启动并切换到前台"); await refresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "启动游戏失败"); }
    finally { setStartingGame(false); }
  }

  return <main>
    <header className="topbar">
      <div className="brand-mark">梦</div>
      <div className="brand-copy"><strong>梦手控制台</strong><span>MACOS AUTOMATION DECK</span></div>
      <div className="topbar-status">
        <StatusDot ok={Boolean(system?.windowFound)} label={system?.windowFound ? "游戏窗口已发现" : "未发现游戏窗口"} />
        <StatusDot ok={Boolean(system?.screenCapturePermission)} label="录屏" />
        <StatusDot ok={Boolean(system?.accessibilityPermission)} label="辅助功能" />
      </div>
    </header>

    <div className="page-shell">
      <section className="hero-panel">
        <div>
          <span className="eyebrow">LOCAL CONTROL · 127.0.0.1</span>
          <h1>{busy ? current?.taskName : "自动化就绪"}</h1>
          <p>{busy ? progress : "选择一个已适配任务开始。运行期间请保持游戏窗口完整可见。"}</p>
        </div>
        <div className="hero-actions">
          <div className={`orb ${busy ? "active" : system?.available ? "ready" : ""}`}><span>{busy ? "运行中" : system?.available ? "待命" : "检查"}</span></div>
          {busy && <button className="button danger" onClick={stopTask}>{current?.status === "stopping" ? "正在停止…" : "停止任务"}</button>}
          {!busy && <button className="button game-button" disabled={startingGame} onClick={startGame}>{startingGame ? "正在启动…" : system?.windowFound ? "唤起游戏" : "启动游戏"}</button>}
        </div>
      </section>

      {system && !system.metricCollectedToday && !dailyDismissed && <section className="daily-reminder">
        <div><span className="reminder-icon">!</span><div><strong>今天还没有数据快照</strong><p>建议每天采集一次，以保持金币和经验趋势连续。采集会自动打开并关闭背包与人物属性页。</p></div></div>
        <div className="reminder-actions"><button className="button ghost" onClick={() => setDailyDismissed(true)}>稍后提醒</button><button className="button primary" disabled={!system.windowFound || busy || capturing} onClick={capture}>{capturing ? "自动采集中…" : "立即自动采集"}</button></div>
      </section>}

      {message && <div className="notice" onClick={() => setMessage("")}>{message}<span>×</span></div>}

      <section className="metric-grid">
        <article className="metric-card featured"><span>当前{metricKind === "gold" ? "金币" : "经验"}</span><strong>{formatNumber(currentValue)}</strong><small>{latest ? `采集于 ${new Date(latest.capturedAt).toLocaleString("zh-CN")}` : "尚无有效采集"}</small></article>
        <article className="metric-card"><span>近 7 天变化</span><strong className={(metrics?.change || 0) >= 0 ? "positive" : "negative"}>{metrics?.change == null ? "—" : `${metrics.change >= 0 ? "+" : ""}${formatNumber(metrics.change)}`}</strong><small>按有效快照计算</small></article>
        <article className="metric-card"><span>任务成功率</span><strong>{summary.total ? `${summary.successRate}%` : "—"}</strong><small>{summary.succeeded} 成功 · {summary.failed} 失败</small></article>
        <article className="metric-card"><span>平均耗时</span><strong>{duration(summary.averageDurationMs)}</strong><small>共运行 {summary.total} 次</small></article>
      </section>

      <section className="dashboard-row">
        <article className="panel chart-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">7 DAY SIGNAL</span><h2>资源趋势</h2></div>
            <div className="segmented"><button className={metricKind === "gold" ? "active" : ""} onClick={() => setMetricKind("gold")}>金币</button><button className={metricKind === "experience" ? "active" : ""} onClick={() => setMetricKind("experience")}>经验</button></div>
          </div>
          <TrendChart data={metrics} />
        </article>
        <article className="panel capture-panel">
          <span className="eyebrow">AUTOMATED SNAPSHOT</span><h2>全自动指标采集</h2>
          <p>在游戏主界面点击采集即可。控制台会自动打开背包读取金币，再打开人物属性读取等级和经验，完成后恢复主界面。</p>
          <div className="capture-state"><span>最近状态</span><strong className={latest?.status === "success" ? "positive" : "negative"}>{latest ? latest.status === "success" ? "采集成功" : "需要处理" : "尚未采集"}</strong></div>
          {latest?.error && <small className="capture-error">{latest.error}</small>}
          <button className="button primary wide" disabled={busy || capturing || !system?.windowFound} onClick={capture}>{capturing ? "正在自动采集…" : "立即自动采集"}</button>
          {latest && <button className="button ghost wide correction-button" disabled={busy} onClick={() => setCorrecting(true)}>修正最近记录</button>}
        </article>
      </section>

      <section className="task-section">
        <div className="section-heading"><div><span className="eyebrow">VERIFIED ON MACOS</span><h2>已适配任务</h2></div><span className="section-note">首版白名单 · {tasks.length} 项</span></div>
        <div className="task-grid">{tasks.map((task, index) => <article className="task-card" key={task.id}>
          <div className="task-number">0{index + 1}</div><span className="eyebrow">{task.eyebrow}</span><h3>{task.name}</h3><p>{task.summary}</p>
          <div className="task-meta"><span>◈ {task.parameters.length} 项运行参数</span><span>◉ 含启动前检查</span></div>
          <button className="task-launch" disabled={busy} onClick={() => setSelected(task)}><span>{busy ? "任务运行中" : "查看说明并启动"}</span><b>→</b></button>
        </article>)}</div>
      </section>

      <section className="dashboard-row lower">
        <article className="panel log-panel">
          <div className="panel-heading"><div><span className="eyebrow">LIVE FEED</span><h2>实时运行日志</h2></div><span className={`live-indicator ${busy ? "on" : ""}`}>{busy ? "LIVE" : "IDLE"}</span></div>
          <div className="log-window">{logs.length ? logs.map((log, index) => <div className={`log-line ${log.level}`} key={`${log.createdAt}-${index}`}><time>{new Date(log.createdAt).toLocaleTimeString("zh-CN", { hour12: false })}</time><span>{log.message}</span></div>) : <div className="log-empty">启动任务后，这里会显示识别与操作进度。</div>}</div>
        </article>
        <article className="panel history-panel">
          <div className="panel-heading"><div><span className="eyebrow">RECENT RUNS</span><h2>最近运行</h2></div></div>
          <div className="history-list">{runs.length ? runs.slice(0, 6).map((run) => <div className="history-row" key={run.id}><span className={`run-icon ${run.status}`}>{run.status === "succeeded" ? "✓" : run.status === "failed" ? "!" : run.status === "cancelled" ? "×" : "·"}</span><div><strong>{run.taskName}</strong><small>{new Date(run.startedAt).toLocaleString("zh-CN")} · {duration(run.durationMs)}</small></div><span className="benefit-tag">未采集收益</span></div>) : <div className="log-empty">还没有运行记录。</div>}</div>
        </article>
      </section>
    </div>
    {selected && <TaskDialog task={selected} busy={busy} onClose={() => setSelected(undefined)} onStart={startTask} />}
    {correcting && latest && <MetricCorrectionDialog snapshot={latest} onClose={() => setCorrecting(false)} onSave={async (values) => { await api.correctMetric(latest.id, values); await refresh(); }} />}
  </main>;
}
