import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { DataPanel } from "./DataPanel";
import { MetricCorrectionDialog } from "./MetricCorrectionDialog";
import { OnboardingDialog } from "./OnboardingDialog";
import { PanelNavigation, type PanelKind } from "./PanelNavigation";
import { TaskDialog } from "./TaskDialog";
import { TaskPanel } from "./TaskPanel";
import type { GameDataResponse, LiveEvent, Metrics, Run, RunSummary, SystemState, Task, TaskInspection } from "./types";

const ACTIVE = new Set(["preparing", "running", "recovering", "stopping"]);
const EMPTY_SUMMARY: RunSummary = { total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 };
const ONBOARDING_KEY = "maa-mhxy-onboarding-v1";

type PlayerNotice = {
  kind: "success" | "error";
  source?: string;
  title: string;
  guidance?: string;
  detail?: string;
  actionLabel?: string;
  onAction?: () => void | Promise<void>;
};

function errorDetail(error: unknown): string | undefined {
  if (error instanceof Error) return error.message;
  return typeof error === "string" ? error : undefined;
}

function GlobalNotice({ notice, onClose }: { notice: PlayerNotice; onClose: () => void }) {
  return <section className={`notice ${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
    <div>
      <strong>{notice.title}</strong>
      {notice.guidance && <p>{notice.guidance}</p>}
      {notice.detail && <details><summary>查看技术详情</summary><p>{notice.detail}</p></details>}
    </div>
    <div>
      {notice.actionLabel && notice.onAction && <button className="button ghost" onClick={() => { onClose(); void notice.onAction?.(); }}>{notice.actionLabel}</button>}
      <button className="button ghost" aria-label="关闭提示" onClick={onClose}>×</button>
    </div>
  </section>;
}

function mergeLogs(...groups: LiveEvent[][]): LiveEvent[] {
  const byId = new Map<string, LiveEvent>();
  groups.flat().forEach((item) => {
    const key = item.id ? String(item.id) : `${item.runId}:${item.createdAt}:${item.type}:${item.message}`;
    byId.set(key, item);
  });
  return [...byId.values()]
    .sort((left, right) => (left.id || 0) - (right.id || 0))
    .slice(-200);
}

function panelFromHash(): PanelKind {
  return window.location.hash === "#data" ? "data" : "tasks";
}

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return <span className={`status-chip ${ok ? "ok" : "muted"}`}><i />{label}</span>;
}

function AutomationWarning({ run, progress, onStop }: { run: Run; progress: string; onStop: () => void }) {
  const recordingResources = run.stage?.startsWith("正在记录");
  const content = recordingResources
    ? { title: run.stage || "正在记录角色资源", body: "正在读取金币、银币和经验，请保持游戏窗口不动。" }
    : run.status === "recovering"
    ? { title: "正在返回主界面", body: "任务已完成，请继续保持游戏窗口不动，等待主界面确认。" }
    : run.status === "stopping"
      ? { title: "正在停止自动操作", body: "停止完成前请勿操作游戏窗口。" }
      : run.status === "preparing"
        ? { title: `${run.taskName}即将开始`, body: "自动化即将控制游戏，请勿点击、移动、缩放、遮挡或最小化游戏窗口。" }
        : { title: `${run.taskName}正在自动完成`, body: "自动化正在控制游戏，请勿点击、移动、缩放、遮挡或最小化游戏窗口。" };

  return <section className={`automation-warning ${run.status}`} role="status" aria-live="polite">
    <div className="automation-warning-mark" aria-hidden="true">!</div>
    <div className="automation-warning-copy">
      <strong>{content.title}</strong>
      <p>{content.body}</p>
      <small>{progress}</small>
    </div>
    <button className="button danger" disabled={run.status === "stopping"} onClick={onStop}>
      {run.status === "stopping" ? "正在停止" : "停止任务"}
    </button>
  </section>;
}

export function App() {
  const [panel, setPanel] = useState<PanelKind>(panelFromHash);
  const [system, setSystem] = useState<SystemState>();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [summary, setSummary] = useState<RunSummary>(EMPTY_SUMMARY);
  const [metricKind, setMetricKind] = useState<"gold" | "experience">("gold");
  const [metricRangeDays, setMetricRangeDays] = useState<7 | 30>(7);
  const [metrics, setMetrics] = useState<Metrics>();
  const [gameData, setGameData] = useState<GameDataResponse>();
  const [selected, setSelected] = useState<Task>();
  const [logs, setLogs] = useState<LiveEvent[]>([]);
  const [notice, setNotice] = useState<PlayerNotice>();
  const [capturing, setCapturing] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [startingGame, setStartingGame] = useState(false);
  const [updatingTaskStatus, setUpdatingTaskStatus] = useState<string>();
  const [dailyDismissed, setDailyDismissed] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(() => window.localStorage.getItem(ONBOARDING_KEY) !== "completed");
  const refreshSequence = useRef(0);

  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current;
    const [systemData, taskData, runData, metricData, gameDataResponse] = await Promise.all([
      api.system(), api.tasks(), api.runs(), api.metrics(metricKind, metricRangeDays), api.gameData()
    ]);
    if (sequence !== refreshSequence.current) return;
    setSystem(systemData); setTasks(taskData.items); setRuns(runData.items); setSummary(runData.summary); setMetrics(metricData); setGameData(gameDataResponse);
    const runId = systemData.run?.id || runData.items[0]?.id;
    if (runId) {
      const history = await api.runEvents(runId);
      if (sequence !== refreshSequence.current) return;
      setLogs((current) => mergeLogs(history.items, current.filter((item) => item.runId === runId)));
    } else {
      setLogs([]);
    }
  }, [metricKind, metricRangeDays]);

  useEffect(() => {
    let midnightTimer: number | undefined;
    let foregroundTimer: number | undefined;
    let refreshInFlight = false;

    const requestRefresh = (showError = false) => {
      if (refreshInFlight) return;
      refreshInFlight = true;
      refresh()
        .catch((error) => {
          if (showError) setNotice({
            kind: "error",
            source: "refresh",
            title: "页面数据暂时没有加载出来",
            guidance: "请确认梦手控制台仍在运行，然后重新加载。",
            detail: errorDetail(error),
            actionLabel: "重新加载",
            onAction: () => requestRefresh(true),
          });
        })
        .finally(() => { refreshInFlight = false; });
    };

    const scheduleMidnightRefresh = () => {
      const now = new Date();
      const nextMidnight = new Date(now);
      nextMidnight.setDate(nextMidnight.getDate() + 1);
      nextMidnight.setHours(0, 0, 0, 0);
      midnightTimer = window.setTimeout(() => {
        requestRefresh();
        scheduleMidnightRefresh();
      }, nextMidnight.getTime() - now.getTime());
    };

    const refreshWhenForegrounded = () => {
      if (document.visibilityState !== "visible") return;
      if (foregroundTimer !== undefined) window.clearTimeout(foregroundTimer);
      foregroundTimer = window.setTimeout(requestRefresh, 100);
    };

    requestRefresh(true);
    scheduleMidnightRefresh();
    document.addEventListener("visibilitychange", refreshWhenForegrounded);
    window.addEventListener("focus", refreshWhenForegrounded);
    return () => {
      if (midnightTimer !== undefined) window.clearTimeout(midnightTimer);
      if (foregroundTimer !== undefined) window.clearTimeout(foregroundTimer);
      document.removeEventListener("visibilitychange", refreshWhenForegrounded);
      window.removeEventListener("focus", refreshWhenForegrounded);
    };
  }, [refresh]);
  useEffect(() => {
    const onHashChange = () => setPanel(panelFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  useEffect(() => {
    const source = new EventSource("/api/events");
    const retryConnection = () => refresh().catch((error) => setNotice({
      kind: "error",
      source: "connection",
      title: "仍然无法读取实时状态",
      guidance: "请确认梦手控制台仍在运行，稍后再试一次。",
      detail: errorDetail(error),
      actionLabel: "重新检测",
      onAction: retryConnection,
    }));
    const receive = (event: MessageEvent) => {
      const item = JSON.parse(event.data) as LiveEvent;
      setLogs((current) => item.id && current.some((entry) => entry.id === item.id) ? current : [...current.slice(-199), item]);
      if (item.type === "status" || item.type === "metric" || item.type === "game_data") refresh().catch(() => undefined);
    };
    ["status", "log", "metric", "game_data", "system"].forEach((type) => source.addEventListener(type, receive));
    source.onerror = () => setNotice({
      kind: "error",
      source: "connection",
      title: "实时状态暂时断开",
      guidance: "正在自动恢复连接；任务不会因此停止，也可以立即重新检测。",
      actionLabel: "重新检测",
      onAction: retryConnection,
    });
    source.onopen = () => {
      setNotice((currentNotice) => currentNotice?.source === "connection" ? undefined : currentNotice);
      refresh().catch(() => undefined);
    };
    return () => source.close();
  }, [refresh]);

  const current = system?.run;
  const busy = Boolean(current && ACTIVE.has(current.status));
  const progress = useMemo(() => current?.stage || (busy ? "自动化运行中" : "等待任务"), [current, busy]);

  function selectPanel(next: PanelKind) {
    window.location.hash = next === "data" ? "data" : "tasks";
    setPanel(next);
  }

  async function startTask(options: Record<string, unknown>) {
    if (!selected) return;
    setLogs([]);
    const run = await api.start(selected.id, options);
    setSystem((state) => state ? { ...state, status: run.status, run } : state);
  }

  async function inspectTask(options: Record<string, unknown>): Promise<TaskInspection> {
    if (!selected) throw new Error("未选择任务");
    return api.inspect(selected.id, options);
  }

  async function inspectOnboarding(): Promise<TaskInspection> {
    const task = tasks.find((item) => item.id === "sanjieqiyuan");
    if (!task) throw new Error("只读画面测试暂时不可用，请重新加载控制台");
    const options = Object.fromEntries(task.parameters.map((item) => [item.key, item.default]));
    return api.inspect(task.id, options);
  }

  function completeOnboarding() {
    window.localStorage.setItem(ONBOARDING_KEY, "completed");
    setOnboardingOpen(false);
  }

  function refreshAfterAction(failureTitle: string) {
    const retry = () => refresh().catch((error) => setNotice({
      kind: "error",
      title: failureTitle,
      guidance: "操作结果不会丢失，请重新加载页面数据。",
      detail: errorDetail(error),
      actionLabel: "重新加载",
      onAction: retry,
    }));
    return retry();
  }

  async function stopTask() {
    if (!current) return;
    try { await api.stop(current.id); }
    catch (error) {
      setNotice({
        kind: "error",
        title: "任务还没有停下来",
        guidance: "请先不要操作游戏，稍等几秒后再试一次。",
        detail: errorDetail(error),
        actionLabel: "重新停止",
        onAction: stopTask,
      });
      return;
    }
    await refreshAfterAction("任务已经停止，但页面没有更新");
  }

  async function capture() {
    setCapturing(true); setNotice(undefined);
    try {
      const result = await api.capture() as { status?: string; error?: string };
      if (result.status === "success") {
        setNotice({ kind: "success", title: "金币和经验已记录" });
        await refreshAfterAction("金币和经验已记录，但页面没有更新");
      } else {
        setNotice({
          kind: "error",
          title: "没有记录到金币和经验",
          guidance: "请将游戏窗口切回无遮挡的主界面，然后重试。",
          detail: result.error,
          actionLabel: "重新记录",
          onAction: capture,
        });
      }
    } catch (error) {
      setNotice({
        kind: "error",
        title: "没有记录到金币和经验",
        guidance: "请确认游戏已打开、窗口没有被遮挡，然后重试。",
        detail: errorDetail(error),
        actionLabel: "重新记录",
        onAction: capture,
      });
    }
    finally { setCapturing(false); }
  }

  async function startGame() {
    setStartingGame(true); setNotice(undefined);
    try {
      await api.startGame();
      setNotice({ kind: "success", title: "梦幻西游已启动并切换到前台" });
      await refreshAfterAction("梦幻西游已启动，但页面没有更新");
    }
    catch (error) {
      setNotice({
        kind: "error",
        title: "没有成功打开梦幻西游",
        guidance: "请确认游戏客户端已经安装且可以正常启动，然后重试。",
        detail: errorDetail(error),
        actionLabel: "重新启动",
        onAction: startGame,
      });
    }
    finally { setStartingGame(false); }
  }

  async function setTaskTodayStatus(taskId: string, status: "completed" | "pending") {
    setUpdatingTaskStatus(taskId); setNotice(undefined);
    const taskName = tasks.find((task) => task.id === taskId)?.name || "任务";
    try {
      const result = await api.setTaskTodayStatus(taskId, status);
      setTasks((currentTasks) => currentTasks.map((task) =>
        task.id === taskId ? { ...task, todayStatus: result.todayStatus } : task
      ));
      setNotice({ kind: "success", title: `${taskName}已标记为${status === "completed" ? "今日已完成" : "今日未完成"}` });
    } catch (error) {
      setNotice({
        kind: "error",
        title: `没有更新${taskName}的完成状态`,
        guidance: "请确认控制台连接正常，然后重试。",
        detail: errorDetail(error),
        actionLabel: "重新标记",
        onAction: () => setTaskTodayStatus(taskId, status),
      });
    } finally {
      setUpdatingTaskStatus(undefined);
    }
  }

  const showReminder = Boolean(system && !system.metricCollectedToday && !dailyDismissed);

  return <main>
    <header className="topbar">
      <div className="brand-mark">梦</div>
      <div className="brand-copy"><strong>梦手控制台</strong><span>梦幻西游挂机助手</span></div>
      <PanelNavigation active={panel} needsCapture={Boolean(system && !system.metricCollectedToday)} onChange={selectPanel} />
      <button className="onboarding-entry" onClick={() => setOnboardingOpen(true)}>设置向导</button>
      <div className="topbar-status">
        <StatusDot ok={Boolean(system?.gameRunning)} label={system?.gameRunning ? "游戏已连接" : "等待游戏"} />
        <StatusDot ok={Boolean(system?.screenCapturePermission)} label={system?.screenCapturePermission ? "画面权限已开启" : "画面权限未开启"} />
        <StatusDot ok={Boolean(system?.accessibilityPermission)} label={system?.accessibilityPermission ? "操作权限已开启" : "操作权限未开启"} />
      </div>
    </header>

    <div className="page-shell">
      {busy && current && <AutomationWarning run={current} progress={progress} onStop={() => { void stopTask(); }} />}

      {showReminder && <section className="daily-reminder compact-reminder">
        <div><span className="reminder-icon">!</span><div><strong>今天还没有记录金币和经验</strong><p>记录一次数据，就能查看今天的收益变化。</p></div></div>
        <div className="reminder-actions"><button className="button ghost" onClick={() => setDailyDismissed(true)}>稍后提醒</button>{panel === "data" ? <button className="button primary" disabled={!system?.available || busy || capturing} onClick={capture}>{capturing ? "正在记录…" : "立即记录"}</button> : <button className="button primary" onClick={() => selectPanel("data")}>前往我的收益</button>}</div>
      </section>}

      {notice && <GlobalNotice notice={notice} onClose={() => setNotice(undefined)} />}

      {panel === "tasks" ? <TaskPanel
        system={system} tasks={tasks} runs={runs} summary={summary} logs={logs} busy={busy} progress={progress}
        startingGame={startingGame} onStartGame={startGame} onStop={stopTask} onSelectTask={setSelected}
        updatingTaskStatus={updatingTaskStatus} onSetTodayStatus={setTaskTodayStatus}
      /> : <DataPanel
        system={system} metrics={metrics} gameData={gameData} metricKind={metricKind} rangeDays={metricRangeDays}
        busy={busy} capturing={capturing}
        onMetricKindChange={setMetricKind} onRangeDaysChange={setMetricRangeDays}
        onCapture={capture}
        onCorrect={() => setCorrecting(true)}
      />}
    </div>
    {selected && <TaskDialog task={selected} busy={busy} onClose={() => setSelected(undefined)} onStart={startTask} onInspect={inspectTask} />}
    {correcting && metrics?.snapshots.at(-1) && <MetricCorrectionDialog snapshot={metrics.snapshots.at(-1)!} onClose={() => setCorrecting(false)} onSave={async (values) => { await api.correctMetric(metrics.snapshots.at(-1)!.id, values); await refresh(); }} />}
    {system && onboardingOpen && <OnboardingDialog
      system={system} busy={busy} startingGame={startingGame}
      onStartGame={startGame} onRefresh={refresh}
      onOpenSettings={async (section) => { await api.openSystemSettings(section); }}
      onInspect={inspectOnboarding} onComplete={completeOnboarding}
      onDismiss={() => setOnboardingOpen(false)}
    />}
  </main>;
}
