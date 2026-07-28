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

function taskActionLabel(system: SystemState | undefined, busy: boolean) {
  if (busy) return "任务运行中";
  if (!system) return "正在检查系统";
  if (!system.gameRunning) return "请先启动游戏";
  if (!system.windowFound) return "请将游戏窗口切到前台";
  if (!system.screenCapturePermission) return "开启屏幕录制权限";
  if (!system.accessibilityPermission) return "开启辅助功能权限";
  if (!system.available) return "等待系统就绪";
  return "查看说明并启动";
}

function runOutcome(run: Run) {
  if (run.status === "preparing") return "准备中";
  if (run.status === "running") return "运行中";
  if (run.status === "stopping") return "正在停止";
  if (run.status === "cancelled") return "已取消";
  if (run.status === "failed") return run.result?.status === "safe_stopped" ? "安全停止" : "失败";
  if (run.result?.status === "already_completed") return "今日已完成";
  if (run.result?.status === "limit_reached") return "达到本次上限";
  return "已完成";
}

function runDetail(run: Run) {
  if (run.error) return run.error;
  const result = run.result;
  if (!result) return null;
  const answered = result.questions ?? result.answers;
  if (answered !== undefined) {
    const clicks = result.clicks !== undefined ? ` · ${result.clicks} 次选择` : "";
    return `已回答 ${answered} 题${clicks}`;
  }
  if (result.rounds !== undefined) {
    const target = result.target_rounds !== undefined ? `/${result.target_rounds}` : "";
    return `已完成 ${result.rounds}${target} 轮`;
  }
  if (result.actions !== undefined) {
    const clicks = result.clicks !== undefined ? ` · ${result.clicks} 次点击` : "";
    return `已执行 ${result.actions} 次自动化操作${clicks}`;
  }
  return null;
}

export function TaskPanel({ system, tasks, runs, summary, logs, busy, progress, startingGame, onStartGame, onStop, onSelectTask }: Props) {
  const current = system?.run;
  const missingPermission = Boolean(system && (!system.screenCapturePermission || !system.accessibilityPermission));
  const completedToday = tasks.filter((task) => task.todayStatus?.status === "completed").length;
  return <div className="panel-page task-panel-page">
    <section className="hero-panel">
      <div><span className="eyebrow">TASK CONTROL · 127.0.0.1</span><h1>{busy ? current?.taskName : "自动化就绪"}</h1><p>{busy ? progress : "选择一个已适配任务开始。运行期间请保持游戏窗口完整可见。"}</p></div>
      <div className="hero-actions">
        <div className={`orb ${busy ? "active" : system?.available ? "ready" : ""}`}><span>{busy ? "运行中" : system?.available ? "待命" : "检查"}</span></div>
        {busy ? <button className="button danger" onClick={onStop}>{current?.status === "stopping" ? "正在停止…" : "停止任务"}</button> : <button className="button game-button" disabled={startingGame} onClick={onStartGame}>{startingGame ? "正在启动…" : system?.gameRunning ? "唤起游戏" : "启动游戏"}</button>}
      </div>
    </section>

    {system?.gameRunning && !system.available && <section className="permission-banner">
      <div><strong>{missingPermission ? "游戏已检测到，自动化权限尚未就绪" : "游戏已检测到，但窗口当前不可访问"}</strong><p>{missingPermission ? "请在 macOS“隐私与安全性”中为运行控制台的应用开启屏幕录制和辅助功能，然后重启控制台。" : "请取消最小化并将梦幻西游窗口切换到前台，然后点击“唤起游戏”重新检查。"}</p></div>
      <span>{!system.screenCapturePermission ? "缺少录屏权限" : !system.accessibilityPermission ? "缺少辅助功能权限" : "等待窗口访问"}</span>
    </section>}

    <section className="task-stat-grid">
      <article><span>任务成功率</span><strong>{summary.total ? `${summary.successRate}%` : "—"}</strong><small>{summary.succeeded} 成功 · {summary.failed} 失败</small></article>
      <article><span>平均耗时</span><strong>{duration(summary.averageDurationMs)}</strong><small>共运行 {summary.total} 次</small></article>
      <article><span>当前阶段</span><strong>{busy ? progress : "等待任务"}</strong><small>{busy ? current?.status : "IDLE"}</small></article>
    </section>

    <section className="task-section">
      <div className="section-heading"><div><span className="eyebrow">VERIFIED ON MACOS</span><h2>已适配任务</h2></div><span className="section-note">今日完成 {completedToday} / {tasks.length}</span></div>
      <div className="task-grid">{tasks.map((task, index) => {
        const isCompleted = task.todayStatus?.status === "completed";
        const isRunning = busy && current?.taskId === task.id;
        const todayLabel = isCompleted ? "今日已完成" : "今日未完成";
        const cardState = isCompleted ? "completed" : isRunning ? "running" : "pending";
        return <article className={`task-card ${cardState}`} key={task.id}>
          <div className="task-number">{String(index + 1).padStart(2, "0")}</div>
          <div className="task-card-statuses">
            <span className={`task-today-status ${isCompleted ? "completed" : "pending"}`}>{todayLabel}</span>
            {isRunning && <span className="task-today-status running">进行中</span>}
          </div>
          <span className="eyebrow">{task.eyebrow}</span><h3>{task.name}</h3><p>{task.summary}</p>
          <div className="task-meta"><span className={`risk-label ${task.risk.level}`}>{task.risk.label} · {task.risk.summary}</span><span>◈ {task.parameters.length} 项运行参数 · ◉ 含启动前检查</span></div>
          <button className="task-launch" disabled={busy || !system?.available} onClick={() => onSelectTask(task)}><span>{taskActionLabel(system, busy)}</span><b>→</b></button>
        </article>;
      })}</div>
    </section>

    <section className="dashboard-row lower">
      <article className="panel log-panel">
        <div className="panel-heading"><div><span className="eyebrow">LIVE FEED</span><h2>实时运行日志</h2></div><span className={`live-indicator ${busy ? "on" : ""}`}>{busy ? "LIVE" : "IDLE"}</span></div>
        <div className="log-window">{logs.length ? logs.map((log, index) => <div className={`log-line ${log.level}`} key={`${log.createdAt}-${index}`}><time>{new Date(log.createdAt).toLocaleTimeString("zh-CN", { hour12: false })}</time><span>{log.message}</span></div>) : <div className="log-empty">启动任务后，这里会显示识别与操作进度。</div>}</div>
      </article>
      <article className="panel history-panel">
        <div className="panel-heading"><div><span className="eyebrow">RECENT RUNS</span><h2>最近运行</h2></div></div>
        <div className="history-list">{runs.length ? runs.slice(0, 6).map((run) => <div className="history-row" key={run.id}><span className={`run-icon ${run.status}`}>{run.status === "succeeded" ? "✓" : run.status === "failed" ? "!" : run.status === "cancelled" ? "×" : "·"}</span><div><strong>{run.taskName}</strong><small>{new Date(run.startedAt).toLocaleString("zh-CN")} · {duration(run.durationMs)}</small>{runDetail(run) && <small className="history-detail">{runDetail(run)}</small>}</div><span className={`run-outcome ${run.status}`}>{runOutcome(run)}</span></div>) : <div className="log-empty">还没有运行记录。</div>}</div>
      </article>
    </section>
  </div>;
}
