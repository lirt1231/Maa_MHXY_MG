import type { LiveEvent, Run, RunSummary, SystemState, Task } from "./types";

function duration(ms?: number | null) {
  if (!ms) return "—";
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  return minutes ? `${minutes}分 ${seconds}秒` : `${seconds}秒`;
}

type Props = {
  system?: SystemState; tasks: Task[]; runs: Run[]; summary: RunSummary; logs: LiveEvent[];
  busy: boolean; progress: string; startingGame: boolean;
  onStartGame: () => void; onStop: () => void; onSelectTask: (task: Task) => void;
};

export function TaskPanel({ system, tasks, runs, summary, logs, busy, progress, startingGame, onStartGame, onStop, onSelectTask }: Props) {
  const current = system?.run;
  return <div className="panel-page task-panel-page">
    <section className="hero-panel">
      <div><span className="eyebrow">TASK CONTROL · 127.0.0.1</span><h1>{busy ? current?.taskName : "自动化就绪"}</h1><p>{busy ? progress : "选择一个已适配任务开始。运行期间请保持游戏窗口完整可见。"}</p></div>
      <div className="hero-actions">
        <div className={`orb ${busy ? "active" : system?.available ? "ready" : ""}`}><span>{busy ? "运行中" : system?.available ? "待命" : "检查"}</span></div>
        {busy ? <button className="button danger" onClick={onStop}>{current?.status === "stopping" ? "正在停止…" : "停止任务"}</button> : <button className="button game-button" disabled={startingGame} onClick={onStartGame}>{startingGame ? "正在启动…" : system?.windowFound ? "唤起游戏" : "启动游戏"}</button>}
      </div>
    </section>

    <section className="task-stat-grid">
      <article><span>任务成功率</span><strong>{summary.total ? `${summary.successRate}%` : "—"}</strong><small>{summary.succeeded} 成功 · {summary.failed} 失败</small></article>
      <article><span>平均耗时</span><strong>{duration(summary.averageDurationMs)}</strong><small>共运行 {summary.total} 次</small></article>
      <article><span>当前阶段</span><strong>{busy ? progress : "等待任务"}</strong><small>{busy ? current?.status : "IDLE"}</small></article>
    </section>

    <section className="task-section">
      <div className="section-heading"><div><span className="eyebrow">VERIFIED ON MACOS</span><h2>已适配任务</h2></div><span className="section-note">首版白名单 · {tasks.length} 项</span></div>
      <div className="task-grid">{tasks.map((task, index) => <article className="task-card" key={task.id}>
        <div className="task-number">0{index + 1}</div><span className="eyebrow">{task.eyebrow}</span><h3>{task.name}</h3><p>{task.summary}</p>
        <div className="task-meta"><span>◈ {task.parameters.length} 项运行参数</span><span>◉ 含启动前检查</span></div>
        <button className="task-launch" disabled={busy} onClick={() => onSelectTask(task)}><span>{busy ? "任务运行中" : "查看说明并启动"}</span><b>→</b></button>
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
  </div>;
}
