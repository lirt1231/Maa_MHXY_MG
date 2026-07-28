import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { DataPanel } from "./DataPanel";
import { MetricCorrectionDialog } from "./MetricCorrectionDialog";
import { PanelNavigation, type PanelKind } from "./PanelNavigation";
import { TaskDialog } from "./TaskDialog";
import { TaskPanel } from "./TaskPanel";
import type { GameDataResponse, LiveEvent, Metrics, Run, RunSummary, SystemState, Task, TaskInspection } from "./types";

const ACTIVE = new Set(["preparing", "running", "stopping"]);
const EMPTY_SUMMARY: RunSummary = { total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 };

function panelFromHash(): PanelKind {
  return window.location.hash === "#data" ? "data" : "tasks";
}

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return <span className={`status-chip ${ok ? "ok" : "muted"}`}><i />{label}</span>;
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
  const [message, setMessage] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [capturingDailyTasks, setCapturingDailyTasks] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [startingGame, setStartingGame] = useState(false);
  const [dailyDismissed, setDailyDismissed] = useState(false);

  const refresh = useCallback(async () => {
    const [systemData, taskData, runData, metricData, gameDataResponse] = await Promise.all([
      api.system(), api.tasks(), api.runs(), api.metrics(metricKind, metricRangeDays), api.gameData()
    ]);
    setSystem(systemData); setTasks(taskData.items); setRuns(runData.items); setSummary(runData.summary); setMetrics(metricData); setGameData(gameDataResponse);
  }, [metricKind, metricRangeDays]);

  useEffect(() => {
    let midnightTimer: number | undefined;
    let foregroundTimer: number | undefined;
    let refreshInFlight = false;

    const requestRefresh = (showError = false) => {
      if (refreshInFlight) return;
      refreshInFlight = true;
      refresh()
        .catch((error) => { if (showError) setMessage(error.message); })
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
    const receive = (event: MessageEvent) => {
      const item = JSON.parse(event.data) as LiveEvent;
      setLogs((current) => item.id && current.some((entry) => entry.id === item.id) ? current : [...current.slice(-199), item]);
      if (item.type === "status" || item.type === "metric" || item.type === "game_data") refresh().catch(() => undefined);
    };
    ["status", "log", "metric", "game_data", "system"].forEach((type) => source.addEventListener(type, receive));
    source.onerror = () => setMessage("实时连接暂时中断，正在自动重连…");
    source.onopen = () => { setMessage(""); refresh().catch(() => undefined); };
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
    const run = await api.start(selected.id, options);
    setSystem((state) => state ? { ...state, status: run.status, run } : state);
    setLogs([]);
  }

  async function inspectTask(options: Record<string, unknown>): Promise<TaskInspection> {
    if (!selected) throw new Error("未选择任务");
    return api.inspect(selected.id, options);
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

  async function captureDailyTasks() {
    setCapturingDailyTasks(true); setMessage("");
    try {
      await api.captureDailyTasks();
      setMessage("今日任务数据已更新");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "今日任务数据采集失败");
    } finally {
      setCapturingDailyTasks(false);
      await refresh().catch(() => undefined);
    }
  }

  async function startGame() {
    setStartingGame(true); setMessage("");
    try { await api.startGame(); setMessage("梦幻西游已启动并切换到前台"); await refresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "启动游戏失败"); }
    finally { setStartingGame(false); }
  }

  const showReminder = Boolean(system && !system.metricCollectedToday && !dailyDismissed);

  return <main>
    <header className="topbar">
      <div className="brand-mark">梦</div>
      <div className="brand-copy"><strong>梦手控制台</strong><span>MACOS AUTOMATION DECK</span></div>
      <PanelNavigation active={panel} needsCapture={Boolean(system && !system.metricCollectedToday)} onChange={selectPanel} />
      <div className="topbar-status">
        <StatusDot ok={Boolean(system?.gameRunning)} label={system?.gameRunning ? "游戏已运行" : "游戏未运行"} />
        <StatusDot ok={Boolean(system?.screenCapturePermission)} label="录屏" />
        <StatusDot ok={Boolean(system?.accessibilityPermission)} label="辅助功能" />
      </div>
    </header>

    <div className="page-shell">
      {showReminder && <section className="daily-reminder compact-reminder">
        <div><span className="reminder-icon">!</span><div><strong>今天还没有数据快照</strong><p>前往数据面板完成一次全自动金币与经验采集。</p></div></div>
        <div className="reminder-actions"><button className="button ghost" onClick={() => setDailyDismissed(true)}>稍后提醒</button>{panel === "data" ? <button className="button primary" disabled={!system?.available || busy || capturing || capturingDailyTasks} onClick={capture}>{capturing ? "自动采集中…" : "立即自动采集"}</button> : <button className="button primary" onClick={() => selectPanel("data")}>前往数据面板</button>}</div>
      </section>}

      {message && <div className="notice" onClick={() => setMessage("")}>{message}<span>×</span></div>}

      {panel === "tasks" ? <TaskPanel
        system={system} tasks={tasks} runs={runs} summary={summary} logs={logs} busy={busy} progress={progress}
        startingGame={startingGame} onStartGame={startGame} onStop={stopTask} onSelectTask={setSelected}
      /> : <DataPanel
        system={system} metrics={metrics} gameData={gameData} metricKind={metricKind} rangeDays={metricRangeDays}
        busy={busy} capturing={capturing} capturingDailyTasks={capturingDailyTasks}
        onMetricKindChange={setMetricKind} onRangeDaysChange={setMetricRangeDays}
        onCapture={capture} onCaptureDailyTasks={captureDailyTasks}
        onCorrect={() => setCorrecting(true)}
      />}
    </div>
    {selected && <TaskDialog task={selected} busy={busy} onClose={() => setSelected(undefined)} onStart={startTask} onInspect={inspectTask} />}
    {correcting && metrics?.snapshots.at(-1) && <MetricCorrectionDialog snapshot={metrics.snapshots.at(-1)!} onClose={() => setCorrecting(false)} onSave={async (values) => { await api.correctMetric(metrics.snapshots.at(-1)!.id, values); await refresh(); }} />}
  </main>;
}
