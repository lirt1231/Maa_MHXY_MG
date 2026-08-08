import { useEffect, useState } from "react";
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
  updatingTaskStatus?: string;
  onSetTodayStatus?: (taskId: string, status: "completed" | "pending") => void;
};

function taskActionLabel(system: SystemState | undefined, busy: boolean, task: Task) {
  if (busy) return "任务运行中";
  if (task.availability?.available === false) {
    return task.availability.reason ?? "当前不可运行";
  }
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
  if (run.status === "recovering") return "正在返回主界面";
  if (run.status === "stopping") return "正在停止";
  if (run.status === "cancelled") return "已停止";
  if (run.status === "failed") return run.failurePhase === "recovery" ? "任务完成，恢复失败" : run.result?.status === "safe_stopped" ? "已停止" : "失败";
  if (run.homeRecovery?.status === "confirmed") return "已确认主界面";
  if (run.result?.status === "already_completed") return "今日已完成";
  if (run.result?.status === "limit_reached") return "达到本次上限";
  return "已完成";
}

function runDetail(run: Run) {
  if (run.failurePhase === "recovery") {
    return run.homeRecovery?.reason || "任务已完成，但未能返回主界面";
  }
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

function formatResourceValue(value?: number | null) {
  return value === null || value === undefined ? "未记录" : value.toLocaleString("zh-CN");
}

function formatResourceDelta(value?: number | null) {
  if (value === null || value === undefined) return "未记录";
  const formatted = Math.abs(value).toLocaleString("zh-CN");
  return value > 0 ? `+${formatted}` : value < 0 ? `-${formatted}` : "0";
}

const RESOURCE_FIELDS = [
  { key: "experience", deltaKey: "experienceDelta", label: "经验" },
  { key: "gold", deltaKey: "goldDelta", label: "金币" },
  { key: "silver", deltaKey: "silverDelta", label: "银币" },
] as const;

function ResourceYieldDetail({ run }: { run: Run }) {
  const resource = run.resourceYield;
  if (!resource) return null;
  if (resource.attributionStatus === "not_run") {
    return <small className="resource-yield-status">本次未执行，不统计资源变化</small>;
  }
  if (resource.attributionStatus === "unavailable") {
    return <small className="resource-yield-status unavailable">资源变化未完整记录</small>;
  }
  if (resource.measurementSource === "settlement_reward") {
    return <details className={`resource-yield-detail ${resource.attributionStatus}`}>
      <summary>结算奖励：金币 {formatResourceDelta(resource.goldDelta)}</summary>
      <dl><div>
        <dt>金币</dt>
        <dd>会试结算页</dd>
        <strong>{formatResourceDelta(resource.goldDelta)}</strong>
      </div></dl>
    </details>;
  }
  const statusLabel = {
    reliable: "净变化",
    partial: "部分变化",
    interrupted: "变化仅供参考",
    cross_level_unknown: "升级期间变化",
  }[resource.attributionStatus];
  const deltaSummary = RESOURCE_FIELDS
    .map((field) => `${field.label} ${formatResourceDelta(resource[field.deltaKey])}`)
    .join(" · ");
  return <details className={`resource-yield-detail ${resource.attributionStatus}`}>
    <summary>{statusLabel}：{deltaSummary}</summary>
    <dl>{RESOURCE_FIELDS.map((field) => <div key={field.key}>
      <dt>{field.label}</dt>
      <dd>{formatResourceValue(resource.before?.values[field.key])} → {formatResourceValue(resource.after?.values[field.key])}</dd>
      <strong>{formatResourceDelta(resource[field.deltaKey])}</strong>
    </div>)}</dl>
    {resource.attributionStatus !== "reliable" && <small>该记录未计入可靠收益统计。</small>}
  </details>;
}

function isStoppedRun(run?: Run | null) {
  return run?.status === "cancelled" || run?.result?.status === "safe_stopped";
}

function hasConfirmedMainScreen(run?: Run | null) {
  return run?.homeRecovery?.status === "confirmed";
}

function currentStatusLabel(run?: Run | null) {
  if (run?.status === "preparing") return "正在准备";
  if (run?.status === "running") return "任务进行中";
  if (run?.status === "recovering") return "正在返回主界面";
  if (run?.status === "stopping") return "停止请求中";
  if (isStoppedRun(run)) {
    return hasConfirmedMainScreen(run) ? "已确认回到主界面" : "现场待确认";
  }
  if (run?.failurePhase === "recovery") return "任务完成但恢复失败，现场待确认";
  if (run?.status === "failed") return "现场待确认";
  if (run?.status === "succeeded") return hasConfirmedMainScreen(run) ? "已确认回到主界面" : "现场待确认";
  return "等待开始";
}

function latestLogSummary(logs: LiveEvent[]) {
  if (!logs.length) return "任务启动后，这里会显示最新进度。";
  const latest = logs[logs.length - 1];
  const needsAttention = latest.level === "error" || latest.level === "warning";
  return `${needsAttention ? "需要留意" : "最新进度"}：${latest.message}`;
}

const FALLBACK_CATEGORY: NonNullable<Task["category"]> = {
  id: "daily",
  label: "日常任务",
  order: 10,
};

function groupedTasks(tasks: Task[]) {
  const groups = new Map<string, {
    category: NonNullable<Task["category"]>;
    tasks: Task[];
  }>();
  tasks.forEach((task) => {
    const category = task.category ?? FALLBACK_CATEGORY;
    const group = groups.get(category.id) ?? { category, tasks: [] };
    group.tasks.push(task);
    groups.set(category.id, group);
  });
  return [...groups.values()]
    .sort((left, right) => left.category.order - right.category.order)
    .map((group) => ({
      ...group,
      tasks: [...group.tasks].sort((left, right) =>
        (left.taskOrder ?? 0) - (right.taskOrder ?? 0)
      ),
    }));
}

type TaskReminder = {
  label: string;
  title: string;
  detail: string;
  task?: Task;
  categoryId?: string;
  weekly?: boolean;
};

function scheduleDistance(task: Task, now: Date) {
  if (!task.schedule?.weekdays.length) return null;
  const currentWeekday = (now.getDay() + 6) % 7;
  return Math.min(...task.schedule.weekdays.map((weekday) =>
    (weekday - currentWeekday + 7) % 7
  ));
}

export function taskReminder(tasks: Task[], now = new Date()): TaskReminder {
  const groups = groupedTasks(tasks);
  const pending = (categoryId: string) => groups
    .find((group) => group.category.id === categoryId)?.tasks
    .filter((task) => task.todayStatus?.status !== "completed") || [];
  const weeklySoon = pending("weekly")
    .map((task) => ({ task, distance: scheduleDistance(task, now) }))
    .filter((item): item is { task: Task; distance: number } => item.distance !== null && item.distance <= 1)
    .sort((left, right) => left.distance - right.distance)[0];
  if (weeklySoon) {
    return {
      label: "周常提醒",
      title: weeklySoon.task.name,
      detail: weeklySoon.distance === 0 ? "今天开放，记得完成" : "明天开放，记得提前安排",
      task: weeklySoon.task,
      categoryId: "weekly",
      weekly: true,
    };
  }
  for (const categoryId of ["daily", "repeatable"]) {
    const task = pending(categoryId).find((item) => item.availability?.available !== false);
    if (task) {
      return {
        label: categoryId === "daily" ? "下一项建议" : "常驻任务提醒",
        title: task.name,
        detail: task.availability?.label || "现在可以开始",
        task,
        categoryId,
      };
    }
  }
  const weekly = pending("weekly")[0];
  if (weekly) {
    return {
      label: "周常提醒",
      title: weekly.name,
      detail: weekly.schedule?.label || weekly.availability?.label || "等待开放",
      task: weekly,
      categoryId: "weekly",
      weekly: true,
    };
  }
  return {
    label: "任务提醒",
    title: tasks.length ? "当前任务已完成" : "等待任务列表",
    detail: tasks.length ? "当前列表没有待完成任务" : "任务加载完成后显示提醒",
  };
}

type TaskCardProps = {
  task: Task;
  number: number;
  system?: SystemState;
  busy: boolean;
  current?: Run | null;
  updatingTaskStatus?: string;
  onSelectTask: (task: Task) => void;
  onSetTodayStatus: (taskId: string, status: "completed" | "pending") => void;
  recommended?: boolean;
};

function TaskCard({ task, number, system, busy, current, updatingTaskStatus, onSelectTask, onSetTodayStatus, recommended = false }: TaskCardProps) {
  const isCompleted = task.todayStatus?.status === "completed";
  const isRunning = busy && current?.taskId === task.id;
  const isUnavailable = task.availability?.available === false;
  const todayLabel = isCompleted ? "今日已完成" : "今日未完成";
  const cardState = isCompleted ? "completed" : isRunning ? "running" : isUnavailable ? "unavailable" : "pending";
  return <article id={`task-card-${task.id}`} className={`task-card ${cardState}${recommended ? " recommended" : ""}`}>
    <div className="task-number">{String(number).padStart(2, "0")}</div>
    <div className="task-card-statuses">
      <span className={`task-today-status ${isCompleted ? "completed" : "pending"}`}>{todayLabel}</span>
      {isRunning && <span className="task-today-status running">进行中</span>}
      <label className="task-completion-toggle" title="手动修改今日完成状态">
        <input
          type="checkbox"
          checked={isCompleted}
          disabled={updatingTaskStatus !== undefined}
          aria-label={`手动修改${task.name}今日完成状态`}
          onChange={(event) => onSetTodayStatus(
            task.id, event.target.checked ? "completed" : "pending"
          )}
        />
        <span>{updatingTaskStatus === task.id ? "保存中" : "完成"}</span>
      </label>
    </div>
    <span className="eyebrow">{task.eyebrow}</span><h3>{task.name}</h3><p>{task.summary}</p>
    <div className="task-meta"><span className={`risk-label ${task.risk.level}`}>{task.risk.label} · {task.risk.summary}</span>{task.availability?.label && <span>{task.availability.label}</span>}<span>{task.parameters.length ? `${task.parameters.length} 项可选设置 · ` : ""}启动前自动检查游戏状态与权限</span></div>
    <button className="task-launch" disabled={busy || !system?.available || isUnavailable} onClick={() => onSelectTask(task)}><span>{taskActionLabel(system, busy, task)}</span><b>→</b></button>
  </article>;
}

export function TaskPanel({ system, tasks, runs, logs, busy, progress, startingGame, onStartGame, onStop, onSelectTask, updatingTaskStatus, onSetTodayStatus = () => undefined }: Props) {
  const [activeCategoryId, setActiveCategoryId] = useState("daily");
  const [highlightedTaskId, setHighlightedTaskId] = useState<string | null>(null);
  const current = system?.run;
  const stopping = current?.status === "stopping";
  const stopped = isStoppedRun(current);
  const recoveryFailed = current?.status === "failed" && current.failurePhase === "recovery";
  const mainScreenConfirmed = stopped && hasConfirmedMainScreen(current);
  const missingPermission = Boolean(system && (!system.screenCapturePermission || !system.accessibilityPermission));
  const completedToday = tasks.filter((task) => task.todayStatus?.status === "completed").length;
  const taskGroups = groupedTasks(tasks);
  const activeGroup = taskGroups.find((group) => group.category.id === activeCategoryId) ?? taskGroups[0];
  const reminder = taskReminder(tasks);
  const reminderDetail = reminder.weekly ? reminder.detail
    : busy ? "当前任务结束后再开始"
    : !system ? "正在检查游戏状态"
    : !system.gameRunning ? "启动游戏后可开始"
    : !system.available ? "游戏就绪后可开始"
    : reminder.detail;
  useEffect(() => {
    if (!highlightedTaskId) return;
    document.getElementById(`task-card-${highlightedTaskId}`)?.scrollIntoView?.({
      behavior: "smooth",
      block: "center",
    });
  }, [activeCategoryId, highlightedTaskId]);
  const openReminder = () => {
    if (!reminder.task || !reminder.categoryId) return;
    setActiveCategoryId(reminder.categoryId);
    setHighlightedTaskId(reminder.task.id);
  };
  return <div className="panel-page task-panel-page">
    <section className="hero-panel">
      <div><span className="eyebrow">今日任务</span><h1>{stopping ? `${current?.taskName ?? "任务"}正在停止` : recoveryFailed ? "任务已完成，现场待确认" : stopped ? `${current?.taskName ?? "任务"}已停止` : busy ? current?.taskName : "可以开始任务"}</h1><p>{stopping ? "停止请求已收到，正在等待自动操作结束。此时请勿操作游戏。" : recoveryFailed ? "任务结果已保留，但未能确认回到主界面。请查看游戏现场后再开始其他任务。" : stopped ? mainScreenConfirmed ? "自动操作已停止，并已确认游戏回到主界面。" : "自动操作已停止，但尚未确认游戏当前页面。请先查看游戏现场，再决定下一步。" : busy ? `${progress}。需要停止时可随时使用右侧按钮。` : "选择一个任务开始，运行期间请保持游戏窗口完整可见。"}</p></div>
      <div className="hero-actions">
        <div className={`orb ${busy ? "active" : system?.available ? "ready" : ""}`}><span>{stopping ? "停止请求中" : stopped ? "已停止输入" : busy ? "执行中" : system?.available ? "可以开始" : "检查中"}</span></div>
        {busy || stopped ? <button className="button danger emergency-stop" onClick={onStop} disabled={stopping || stopped}>{stopping ? "正在停止自动操作…" : stopped ? "自动操作已停止" : "停止任务"}</button> : <button className="button game-button" disabled={startingGame} onClick={onStartGame}>{startingGame ? "正在启动…" : system?.gameRunning ? "唤起游戏" : "启动游戏"}</button>}
      </div>
    </section>

    {system?.gameRunning && !system.available && <section className="permission-banner">
      <div><strong>{missingPermission ? "游戏已检测到，自动化权限尚未就绪" : "游戏已检测到，但窗口当前不可访问"}</strong><p>{missingPermission ? "请在 macOS“隐私与安全性”中为运行控制台的应用开启屏幕录制和辅助功能，开启后返回这里重新检测。" : "请取消最小化并将梦幻西游窗口切换到前台，然后重新检测窗口。"}</p></div>
      <div className="permission-actions"><span>{!system.screenCapturePermission ? "缺少录屏权限" : !system.accessibilityPermission ? "缺少辅助功能权限" : "等待窗口访问"}</span><button className="button ghost" disabled={startingGame} onClick={onStartGame}>{startingGame ? "正在重新检测…" : missingPermission ? "重新检测权限" : "重新检测窗口"}</button></div>
    </section>}

    <section className="task-stat-grid">
      <article className="today-progress-card"><span>今日进度</span><strong>{completedToday} / {tasks.length}</strong><div className="today-progress-track" aria-label={`今日已完成 ${completedToday} 项，共 ${tasks.length} 项`}><i style={{ width: `${tasks.length ? completedToday / tasks.length * 100 : 0}%` }} /></div><small>{completedToday === tasks.length && tasks.length ? "今天的任务已全部完成" : `还有 ${Math.max(0, tasks.length - completedToday)} 项待完成`}</small></article>
      {reminder.task ? <button
        type="button"
        className={`task-reminder-card${reminder.weekly ? " weekly" : ""}`}
        aria-label={`${reminder.label}：${reminder.title}`}
        onClick={openReminder}
      ><span>{reminder.label}</span><strong>{reminder.title}<b aria-hidden="true">→</b></strong><small>{reminderDetail}</small></button> : <article className="task-reminder-card empty"><span>{reminder.label}</span><strong>{reminder.title}</strong><small>{reminderDetail}</small></article>}
      <article><span>当前状态</span><strong>{stopping ? "正在停止自动操作" : recoveryFailed ? "任务完成但恢复失败" : stopped ? "已停止输入" : busy ? progress : "等待选择任务"}</strong><small>{currentStatusLabel(busy || stopped || recoveryFailed ? current : undefined)}</small></article>
    </section>

    <section className="task-section">
      <div className="section-heading"><div><span className="eyebrow">可自动完成</span><h2>全部任务</h2></div><span className="section-note">今日完成 {completedToday} / {tasks.length}</span></div>
      <div className="task-category-tabs" role="tablist" aria-label="任务分类">{taskGroups.map((group) => {
        const completedInGroup = group.tasks.filter((task) => task.todayStatus?.status === "completed").length;
        const selected = group.category.id === activeGroup?.category.id;
        return <button
          type="button"
          role="tab"
          id={`task-tab-${group.category.id}`}
          aria-controls={`task-panel-${group.category.id}`}
          aria-selected={selected}
          aria-label={`${group.category.label}，完成 ${completedInGroup} / ${group.tasks.length}`}
          className={selected ? "active" : ""}
          key={group.category.id}
          onClick={() => setActiveCategoryId(group.category.id)}
        >
          <span>{group.category.label}</span>
          <small>{completedInGroup} / {group.tasks.length}</small>
        </button>;
      })}</div>
      {activeGroup && <section
        className="task-tab-panel"
        role="tabpanel"
        id={`task-panel-${activeGroup.category.id}`}
        aria-labelledby={`task-tab-${activeGroup.category.id}`}
      >
          <div className="task-grid">{activeGroup.tasks.map((task, index) => <TaskCard
            key={task.id}
            task={task}
            number={index + 1}
            system={system}
            busy={busy}
            current={current}
            updatingTaskStatus={updatingTaskStatus}
            onSelectTask={onSelectTask}
            onSetTodayStatus={onSetTodayStatus}
            recommended={reminder.task?.id === task.id}
          />)}</div>
      </section>}
    </section>

    <section className="dashboard-row lower">
      <article className="panel log-panel">
        <div className="panel-heading"><div><span className="eyebrow">最新进度</span><h2>任务动态</h2></div><span className={`live-indicator ${busy ? "on" : ""}`}>{busy ? "实时更新" : "等待开始"}</span></div>
        <p className="log-summary" aria-live="polite">{latestLogSummary(logs)}</p>
        <details className="run-details">
          <summary>查看运行详情</summary>
          <div className="log-window">{logs.length ? logs.map((log, index) => <div className={`log-line ${log.level}`} key={`${log.createdAt}-${index}`}><time>{new Date(log.createdAt).toLocaleTimeString("zh-CN", { hour12: false })}</time><span>{log.message}</span></div>) : <div className="log-empty">本次运行还没有详细记录。</div>}</div>
        </details>
      </article>
      <article className="panel history-panel">
        <div className="panel-heading"><div><span className="eyebrow">运行记录</span><h2>最近任务</h2></div></div>
        <div className="history-list">{runs.length ? runs.slice(0, 6).map((run) => <div className="history-row" key={run.id}><span className={`run-icon ${run.status}`}>{run.status === "succeeded" ? "✓" : run.status === "failed" ? "!" : run.status === "cancelled" ? "×" : "·"}</span><div><strong>{run.taskName}</strong><small>{new Date(run.startedAt).toLocaleString("zh-CN")} · {duration(run.durationMs)}</small>{runDetail(run) && <small className="history-detail">{runDetail(run)}</small>}<ResourceYieldDetail run={run} /></div><span className={`run-outcome ${run.status}`}>{runOutcome(run)}</span></div>) : <div className="log-empty">还没有运行记录。</div>}</div>
      </article>
    </section>
  </div>;
}
