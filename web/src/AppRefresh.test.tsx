import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const apiMock = vi.hoisted(() => ({
  system: vi.fn(),
  tasks: vi.fn(),
  runs: vi.fn(),
  runEvents: vi.fn(),
  metrics: vi.fn(),
  taskYields: vi.fn(),
  gameData: vi.fn(),
  start: vi.fn(),
  inspect: vi.fn(),
  stop: vi.fn(),
  capture: vi.fn(),
  captureDailyTasks: vi.fn(),
  startGame: vi.fn(),
  openSystemSettings: vi.fn(),
  correctMetric: vi.fn(),
  submitManualReview: vi.fn(),
}));

vi.mock("./api", () => ({ api: apiMock }));

class EventSourceStub {
  static current: EventSourceStub | undefined;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  listeners = new Map<string, (event: MessageEvent) => void>();

  constructor(_url: string) { EventSourceStub.current = this; }
  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, listener);
  }
  emit(type: string, event: object) {
    this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(event) }));
  }
  close() {}
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

async function flushRefresh() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("App daily refresh", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", EventSourceStub);
    window.location.hash = "";
    window.localStorage.setItem("maa-mhxy-onboarding-v1", "completed");
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    apiMock.system.mockResolvedValue({
      platform: "darwin",
      gameRunning: false,
      windowFound: false,
      screenCapturePermission: true,
      accessibilityPermission: true,
      available: false,
      status: "idle",
      metricCollectedToday: true,
    });
    apiMock.tasks.mockResolvedValue({ items: [] });
    apiMock.runs.mockResolvedValue({
      items: [],
      summary: { total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 },
    });
    apiMock.runEvents.mockResolvedValue({ items: [] });
    apiMock.metrics.mockResolvedValue(undefined);
    apiMock.taskYields.mockResolvedValue({ rangeDays: 30, tasks: [] });
    apiMock.gameData.mockResolvedValue(undefined);
    apiMock.openSystemSettings.mockResolvedValue({ opened: true, section: "screen-capture" });
    apiMock.stop.mockResolvedValue({ status: "stopping" });
  });

  afterEach(() => {
    cleanup();
    EventSourceStub.current = undefined;
    vi.unstubAllGlobals();
    vi.useRealTimers();
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("restores a pending xiangshi manual review and submits its request id", async () => {
    const run = {
      id: "xiangshi-run", taskId: "keju", taskName: "科举乡试", status: "running",
      stage: "等待人工选择乡试答案", options: {}, startedAt: "2026-08-13T00:00:00Z",
    };
    apiMock.system.mockResolvedValueOnce({
      platform: "darwin", gameRunning: true, windowFound: true, screenCapturePermission: true,
      accessibilityPermission: true, available: true, status: "running", metricCollectedToday: true, run,
    });
    apiMock.runs.mockResolvedValueOnce({ items: [run], summary: { total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 } });
    apiMock.runEvents.mockResolvedValueOnce({ items: [{
      id: 10, runId: "xiangshi-run", createdAt: "2026-08-13T00:00:00Z", level: "warning", type: "manual_review",
      message: "乡试需要人工选择答案", payload: {
        requestId: "request-1", question: "常识题", options: { A: "甲", B: "乙", C: "丙", D: "丁" }, review: {},
      },
    }] });
    apiMock.submitManualReview.mockResolvedValue({ status: "submitted" });

    render(<App />);
    await flushRefresh();
    fireEvent.click(screen.getByRole("radio", { name: "C 丙" }));
    fireEvent.click(screen.getByRole("button", { name: "确认答案" }));
    await flushRefresh();

    expect(apiMock.submitManualReview).toHaveBeenCalledWith("xiangshi-run", "request-1", "C");
  });

  it("stops the xiangshi run from its manual review dialog", async () => {
    const run = {
      id: "xiangshi-run", taskId: "keju", taskName: "科举乡试", status: "running",
      stage: "等待人工选择乡试答案", options: {}, startedAt: "2026-08-13T00:00:00Z",
    };
    apiMock.system.mockResolvedValueOnce({
      platform: "darwin", gameRunning: true, windowFound: true, screenCapturePermission: true,
      accessibilityPermission: true, available: true, status: "running", metricCollectedToday: true, run,
    });
    apiMock.runs.mockResolvedValueOnce({ items: [run], summary: { total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 } });
    apiMock.runEvents.mockResolvedValueOnce({ items: [{
      id: 10, runId: "xiangshi-run", createdAt: "2026-08-13T00:00:00Z", level: "warning", type: "manual_review",
      message: "乡试需要人工选择答案", payload: {
        requestId: "request-1", question: "常识题", options: { A: "甲", B: "乙" }, review: {},
      },
    }] });

    render(<App />);
    await flushRefresh();
    fireEvent.click(screen.getByRole("button", { name: "退出答题" }));
    await flushRefresh();

    expect(apiMock.stop).toHaveBeenCalledWith("xiangshi-run");
    expect(screen.queryByRole("dialog", { name: "乡试人工复核" })).toBeNull();
  });

  it("refreshes once after the next local midnight and clears the timer on unmount", async () => {
    vi.setSystemTime(new Date(2026, 6, 28, 23, 59, 59, 500));
    const view = render(<App />);
    await flushRefresh();
    expect(apiMock.tasks).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(apiMock.tasks).toHaveBeenCalledTimes(2);

    view.unmount();
    await vi.advanceTimersByTimeAsync(24 * 60 * 60 * 1000);
    expect(apiMock.tasks).toHaveBeenCalledTimes(2);
  });

  it("coalesces visibility and focus events into one foreground refresh", async () => {
    render(<App />);
    await flushRefresh();
    expect(apiMock.tasks).toHaveBeenCalledTimes(1);

    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });

    expect(apiMock.tasks).toHaveBeenCalledTimes(2);
  });

  it("does not let an older live refresh overwrite a completed task", async () => {
    const pendingTask = {
      id: "fuli_qiandao", name: "福利签到", eyebrow: "每日福利", summary: "完成签到",
      description: "完成签到", status: "ready",
      risk: { level: "low", label: "低风险", summary: "每日签到" },
      steps: [], checklist: [], warnings: [], parameters: [],
      todayStatus: { status: "pending", completedAt: null, resultStatus: null },
    };
    const completedTask = {
      ...pendingTask,
      todayStatus: {
        status: "completed", completedAt: "2026-08-03T01:00:56Z", resultStatus: "completed",
      },
    };
    apiMock.tasks.mockResolvedValueOnce({ items: [pendingTask] });
    render(<App />);
    await flushRefresh();

    const older = deferred<{ items: typeof pendingTask[] }>();
    const latest = deferred<{ items: typeof completedTask[] }>();
    apiMock.tasks.mockReturnValueOnce(older.promise).mockReturnValueOnce(latest.promise);
    const statusEvent = {
      id: 1, runId: "welfare-run", createdAt: "2026-08-03T01:00:56Z",
      level: "info", type: "status", message: "福利签到完成", payload: {},
    };
    EventSourceStub.current?.emit("status", statusEvent);
    EventSourceStub.current?.emit("status", { ...statusEvent, id: 2 });

    latest.resolve({ items: [completedTask] });
    await flushRefresh();
    expect(screen.getByText("今日已完成")).toBeTruthy();

    older.resolve({ items: [pendingTask] });
    await flushRefresh();
    expect(screen.getByText("今日已完成")).toBeTruthy();
    expect(screen.queryByText("今日未完成")).toBeNull();
  });

  it("shows player-facing brand, navigation, and connection labels", async () => {
    render(<App />);
    await flushRefresh();

    expect(screen.getByText("梦幻西游挂机助手")).toBeTruthy();
    expect(screen.getByText("任务中心")).toBeTruthy();
    expect(screen.getByText("我的收益")).toBeTruthy();
    expect(screen.getByText("等待游戏")).toBeTruthy();
    expect(screen.getByText("画面权限已开启")).toBeTruthy();
    expect(screen.getByText("操作权限已开启")).toBeTruthy();
  });

  it("keeps the active automation warning visible across panels and can stop", async () => {
    apiMock.system.mockResolvedValue({
      platform: "darwin", gameRunning: true, windowFound: true,
      screenCapturePermission: true, accessibilityPermission: true,
      available: true, status: "running", metricCollectedToday: true,
      run: {
        id: "baotu-run", taskId: "baotu", taskName: "宝图任务", status: "running",
        stage: "正在等待游戏自动完成", options: {}, startedAt: "2026-08-03T01:00:00Z",
      },
    });

    render(<App />);
    await flushRefresh();

    expect(screen.getByText("宝图任务正在自动完成")).toBeTruthy();
    expect(screen.getByText("自动化正在控制游戏，请勿点击、移动、缩放、遮挡或最小化游戏窗口。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "我的收益" }));
    expect(screen.getByText("宝图任务正在自动完成")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "停止任务" }));
    await flushRefresh();
    expect(apiMock.stop).toHaveBeenCalledWith("baotu-run");
  });

  it("uses recovery-specific window warning copy", async () => {
    apiMock.system.mockResolvedValue({
      platform: "darwin", gameRunning: true, windowFound: true,
      screenCapturePermission: true, accessibilityPermission: true,
      available: true, status: "recovering", metricCollectedToday: true,
      run: {
        id: "recovery-run", taskId: "shimen", taskName: "师门任务", status: "recovering",
        stage: "正在确认主界面", options: {}, startedAt: "2026-08-03T01:00:00Z",
        homeRecovery: { status: "running" },
      },
    });

    render(<App />);
    await flushRefresh();

    expect(screen.getAllByText("正在返回主界面").length).toBeGreaterThan(0);
    expect(screen.getByText("任务已完成，请继续保持游戏窗口不动，等待主界面确认。")).toBeTruthy();
  });

  it("completes and persists the first-run read-only test", async () => {
    window.localStorage.clear();
    apiMock.system.mockResolvedValue({
      platform: "darwin", gameRunning: true, windowFound: true,
      screenCapturePermission: true, accessibilityPermission: true,
      available: true, status: "idle", metricCollectedToday: true,
    });
    apiMock.tasks.mockResolvedValue({ items: [{
      id: "sanjieqiyuan", name: "三界奇缘", eyebrow: "每日答题", summary: "完成答题",
      description: "完成答题", status: "ready",
      risk: { level: "low", label: "低风险", summary: "仅答题" },
      steps: [], checklist: [], warnings: [],
      parameters: [{ key: "maxSteps", label: "最多题数", type: "integer", default: 10 }],
      todayStatus: { status: "pending", completedAt: null, resultStatus: null },
    }] });
    apiMock.inspect.mockResolvedValue({
      taskId: "sanjieqiyuan", status: "ready", canStart: true,
      scene: "click_activity", sceneLabel: "游戏主界面", summary: "可以开始",
      evidence: { imageSize: [1020, 718] }, observedAt: "2026-08-02T00:00:00Z",
    });

    render(<App />);
    await flushRefresh();
    fireEvent.click(screen.getByRole("button", { name: "开始只读画面测试" }));
    await flushRefresh();
    expect(screen.getByText("画面测试通过")).toBeTruthy();
    expect(apiMock.inspect).toHaveBeenCalledWith("sanjieqiyuan", { maxSteps: 10 });
    fireEvent.click(screen.getByRole("button", { name: "进入任务中心" }));

    expect(window.localStorage.getItem("maa-mhxy-onboarding-v1")).toBe("completed");
    expect(screen.queryByRole("dialog", { name: "已经可以使用自动任务" })).toBeNull();
  });

  it("explains refresh errors, folds technical details, and retries", async () => {
    apiMock.system.mockRejectedValueOnce(new Error("Failed to fetch"));
    render(<App />);
    await flushRefresh();

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("页面数据暂时没有加载出来")).toBeTruthy();
    expect(screen.getByText("请确认梦手控制台仍在运行，然后重新加载。")).toBeTruthy();
    const details = screen.getByText("查看技术详情").closest("details");
    expect(details?.open).toBe(false);
    expect(screen.getByText("Failed to fetch")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await flushRefresh();
    expect(apiMock.system).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("restores the latest run logs after a page reload", async () => {
    apiMock.runs.mockResolvedValue({
      items: [{ id: "latest-run", taskId: "kaogu", taskName: "工坊考古", status: "succeeded", options: {}, startedAt: "2026-08-02T00:00:00Z" }],
      summary: { total: 1, succeeded: 1, failed: 0, successRate: 100, averageDurationMs: 1000 },
    });
    apiMock.runEvents.mockResolvedValue({
      items: [{ id: 42, runId: "latest-run", createdAt: "2026-08-02T00:01:00Z", level: "info", type: "log", message: "恢复的任务日志", payload: {} }],
    });

    render(<App />);
    await flushRefresh();

    expect(apiMock.runEvents).toHaveBeenCalledWith("latest-run");
    expect(screen.getByText("恢复的任务日志")).toBeTruthy();
  });
});
