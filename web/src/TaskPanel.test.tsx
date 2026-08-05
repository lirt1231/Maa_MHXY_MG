import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { taskReminder, TaskPanel } from "./TaskPanel";
import type { Run, SystemState, Task } from "./types";

const system: SystemState = {
  platform: "darwin", gameRunning: true, windowFound: true,
  screenCapturePermission: true, accessibilityPermission: true,
  available: true, status: "idle", metricCollectedToday: true
};

function run(overrides: Partial<Run>): Run {
  return {
    id: "run", taskId: "sanjieqiyuan", taskName: "三界奇缘",
    status: "succeeded", options: {}, startedAt: "2026-07-26T00:00:00+00:00",
    durationMs: 1000, ...overrides
  };
}

function categorizedTask(
  id: string,
  name: string,
  category: NonNullable<Task["category"]>,
  taskOrder: number,
  completed = false,
): Task {
  return {
    id, name, category, taskOrder, eyebrow: "任务", summary: `${name}摘要`,
    description: `${name}说明`, status: "experimental",
    risk: { level: "low", label: "低风险", summary: "测试任务" },
    steps: [], checklist: [], warnings: [], parameters: [],
    todayStatus: {
      status: completed ? "completed" : "pending",
      completedAt: completed ? "2026-08-04T01:00:00Z" : null,
      resultStatus: completed ? "completed" : null,
    },
  };
}

afterEach(cleanup);

describe("TaskPanel run outcomes", () => {
  it("shows task categories as tabs and orders the active category", () => {
    const daily = { id: "daily", label: "日常任务", order: 10 } as const;
    const weekly = { id: "weekly", label: "周常任务", order: 20 } as const;
    const repeatable = { id: "repeatable", label: "常驻任务", order: 30 } as const;
    const tasks = [
      categorizedTask("kaogu", "工坊考古", repeatable, 20),
      categorizedTask("keju", "科举乡试", daily, 40),
      categorizedTask("huishi", "科举会试", weekly, 10),
      categorizedTask("fuli", "福利签到", daily, 10, true),
      categorizedTask("zhuogui", "捉鬼任务", repeatable, 10),
    ];

    const view = render(<TaskPanel
      system={system} tasks={tasks} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    const tabs = view.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "日常任务1 / 2", "周常任务0 / 1", "常驻任务0 / 2",
    ]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect([...view.container.querySelectorAll(".task-card h3")].map((heading) => heading.textContent)).toEqual([
      "福利签到", "科举乡试",
    ]);

    fireEvent.click(view.getByRole("tab", { name: "常驻任务，完成 0 / 2" }));
    expect([...view.container.querySelectorAll(".task-card h3")].map((heading) => heading.textContent)).toEqual([
      "捉鬼任务", "工坊考古",
    ]);
    expect(view.queryByRole("heading", { name: "福利签到" })).toBeNull();

    fireEvent.click(view.getByRole("tab", { name: "周常任务，完成 0 / 1" }));
    expect(view.getByRole("heading", { name: "科举会试" })).toBeTruthy();
    expect(view.queryByRole("heading", { name: "工坊考古" })).toBeNull();
  });

  it("replaces average duration with an actionable task reminder", () => {
    const daily = { id: "daily", label: "日常任务", order: 10 } as const;
    const repeatable = { id: "repeatable", label: "常驻任务", order: 30 } as const;
    const tasks = [
      categorizedTask("fuli", "福利签到", daily, 10, true),
      categorizedTask("zhuogui", "捉鬼任务", repeatable, 10),
    ];
    const view = render(<TaskPanel
      system={system} tasks={tasks} runs={[]}
      summary={{ total: 5, succeeded: 5, failed: 0, successRate: 100, averageDurationMs: 65000 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.queryByText("最近平均耗时")).toBeNull();
    fireEvent.click(view.getByRole("button", { name: "常驻任务提醒：捉鬼任务" }));
    expect(view.getByRole("tab", { name: "常驻任务，完成 0 / 1" }).getAttribute("aria-selected")).toBe("true");
    expect(view.getByRole("heading", { name: "捉鬼任务" }).closest("article")?.className).toContain("recommended");
  });

  it("does not claim a reminded task can start before the game is ready", () => {
    const daily = { id: "daily", label: "日常任务", order: 10 } as const;
    const view = render(<TaskPanel
      system={{ ...system, gameRunning: false, available: false }}
      tasks={[categorizedTask("fuli", "福利签到", daily, 10)]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    const reminder = view.getByRole("button", { name: "下一项建议：福利签到" });
    expect(reminder.textContent).toContain("启动游戏后可开始");
    expect(reminder.textContent).not.toContain("现在可以开始");
  });

  it("prioritizes a weekly task on the day before it opens", () => {
    const weekly = { id: "weekly", label: "周常任务", order: 20 } as const;
    const huishi = categorizedTask("huishi", "科举会试", weekly, 10);
    huishi.schedule = {
      weekdays: [5],
      label: "仅每周六开放",
      unavailableReason: "请在周六运行",
    };

    expect(taskReminder([huishi], new Date(2026, 7, 7, 12))).toMatchObject({
      label: "周常提醒",
      title: "科举会试",
      detail: "明天开放，记得提前安排",
      categoryId: "weekly",
    });
  });

  it("uses user-facing wording for live task progress", () => {
    const view = render(<TaskPanel
      system={system} tasks={[]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getByText("任务动态")).toBeTruthy();
    expect(view.getByText("最新进度")).toBeTruthy();
    expect(view.container.querySelector(".live-indicator")?.textContent).toBe("实时更新");
    expect(view.queryByText("实时运行日志")).toBeNull();
    expect(view.queryByText("LIVE")).toBeNull();
  });

  it("does not present the previous successful run as the current state", () => {
    const view = render(<TaskPanel
      system={{ ...system, run: run({ status: "succeeded" }) }} tasks={[]} runs={[]}
      summary={{ total: 1, succeeded: 1, failed: 0, successRate: 100, averageDurationMs: 1000 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    const statusCard = view.getByText("当前状态").closest("article")!;
    expect(statusCard.textContent).toContain("等待选择任务");
    expect(statusCard.textContent).toContain("等待开始");
    expect(statusCard.textContent).not.toContain("已完成");
  });

  it("uses player-facing status and stop wording without exposing technical labels", () => {
    const onStop = vi.fn();
    const activeSystem = { ...system, run: run({ status: "running" }) };
    const view = render(<TaskPanel
      system={activeSystem} tasks={[]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy progress="正在寻找答题入口" startingGame={false}
      onStartGame={vi.fn()} onStop={onStop} onSelectTask={vi.fn()}
    />);

    expect(view.getByText("任务进行中")).toBeTruthy();
    expect(view.queryByText("IDLE")).toBeNull();
    expect(view.queryByText(/TASK CONTROL|RECENT RUNS|VERIFIED ON MACOS/)).toBeNull();
    expect(view.getByText(/需要停止时可随时使用右侧按钮/)).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "停止任务" }));
    expect(onStop).toHaveBeenCalledOnce();
  });

  it("offers an explicit permission recheck action", () => {
    const onStartGame = vi.fn();
    const view = render(<TaskPanel
      system={{ ...system, available: false, screenCapturePermission: false }} tasks={[]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={onStartGame} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getByText(/开启后返回这里重新检测/)).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "重新检测权限" }));
    expect(onStartGame).toHaveBeenCalledOnce();
  });

  it("keeps a visible disabled stop control while a stop request is pending", () => {
    const activeSystem = { ...system, run: run({ status: "stopping", stage: "正在等待进程退出" }) };
    const view = render(<TaskPanel
      system={activeSystem} tasks={[]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy progress="正在等待进程退出" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getAllByText("停止请求中").length).toBeGreaterThan(0);
    expect(view.getByText(/正在等待自动操作结束/)).toBeTruthy();
    const button = view.getByRole("button", { name: "正在停止自动操作…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(view.queryByRole("button", { name: "启动游戏" })).toBeNull();
  });

  it("reports stopped input without claiming the game returned to the main screen", () => {
    const stoppedSystem = { ...system, run: run({ status: "cancelled", stage: "已停止" }) };
    const view = render(<TaskPanel
      system={stoppedSystem} tasks={[]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getAllByText("已停止输入").length).toBeGreaterThan(0);
    expect(view.getByText("现场待确认")).toBeTruthy();
    expect(view.getByText(/尚未确认游戏当前页面/)).toBeTruthy();
    expect(view.queryByText("已确认回到主界面")).toBeNull();
    expect((view.getByRole("button", { name: "自动操作已停止" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("only confirms the main screen from structured recovery evidence", () => {
    const stoppedSystem = { ...system, run: run({
      status: "cancelled", stage: "任意文本",
      homeRecovery: { status: "confirmed", outcome: "recovered", scene: "main_screen" }
    }) };
    const view = render(<TaskPanel
      system={stoppedSystem} tasks={[]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getByText("已确认回到主界面")).toBeTruthy();
    expect(view.getByText(/已确认游戏回到主界面/)).toBeTruthy();
    expect(view.queryByText("现场待确认")).toBeNull();
  });

  it("shows the dedicated recovery phase while it is active", () => {
    const recoveringSystem = { ...system, run: run({
      status: "recovering", stage: "正在返回主界面",
      homeRecovery: { status: "running" }
    }) };
    const view = render(<TaskPanel
      system={recoveringSystem} tasks={[]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy progress="正在返回主界面" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getAllByText("正在返回主界面").length).toBeGreaterThan(0);
    expect(view.getByRole("button", { name: "停止任务" })).toBeTruthy();
  });

  it("reports a completed task whose recovery failed as awaiting inspection", () => {
    const failedSystem = { ...system, run: run({
      status: "failed", failurePhase: "recovery",
      result: { task_id: "sanjieqiyuan", status: "completed", questions: 10, clicks: 10 },
      homeRecovery: { status: "failed", reason: "未知页面" }
    }) };
    const view = render(<TaskPanel
      system={failedSystem} tasks={[]} runs={[]}
      summary={{ total: 1, succeeded: 0, failed: 1, successRate: 0, averageDurationMs: 1000 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getByText("任务已完成，现场待确认")).toBeTruthy();
    expect(view.getByText("任务完成但恢复失败")).toBeTruthy();
    expect(view.getByText("任务完成但恢复失败，现场待确认")).toBeTruthy();
  });

  it("keeps full logs in a collapsed detail while showing the latest issue", () => {
    const view = render(<TaskPanel
      system={system} tasks={[]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[
        { runId: "run", createdAt: "2026-07-26T00:00:00+00:00", level: "info", type: "log", message: "已打开活动页面", payload: {} },
        { runId: "run", createdAt: "2026-07-26T00:00:01+00:00", level: "info", type: "log", message: "正在重新检查页面", payload: {} },
        { runId: "run", createdAt: "2026-07-26T00:00:02+00:00", level: "warning", type: "log", message: "暂时没有识别到开始按钮", payload: {} },
      ]} busy progress="正在检查页面" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getByText("需要留意：暂时没有识别到开始按钮")).toBeTruthy();
    const details = view.getByText("查看运行详情").closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(details.querySelectorAll(".log-line")).toHaveLength(3);
    fireEvent.click(view.getByText("查看运行详情"));
    expect(details.open).toBe(true);
    expect(view.getByText("正在重新检查页面")).toBeTruthy();
  });

  it("renders xiangshi and huishi as independent task cards", () => {
    const keju: Task = {
      id: "keju", name: "科举乡试", eyebrow: "每日乡试", summary: "完成乡试答题",
      description: "打开乡试正式答题页", status: "experimental",
      risk: { level: "low", label: "低风险", summary: "仅答题" },
      steps: ["打开乡试答题页"], checklist: ["乡试题目可见"], warnings: [], parameters: [],
      todayStatus: { status: "pending", completedAt: null, resultStatus: null },
    };
    const huishi: Task = {
      ...keju, id: "keju_huishi", name: "科举会试", eyebrow: "限时会试",
      summary: "完成会试答题", description: "打开会试正式答题页",
      steps: ["打开会试答题页"], checklist: ["会试题目可见"],
    };
    const onSelectTask = vi.fn();
    const view = render(<TaskPanel
      system={system} tasks={[keju, huishi]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={onSelectTask}
    />);

    expect(view.getByRole("heading", { name: "科举乡试" })).toBeTruthy();
    const huishiHeading = view.getByRole("heading", { name: "科举会试" });
    expect(huishiHeading).toBeTruthy();
    fireEvent.click(huishiHeading.closest("article")!.querySelector("button")!);
    expect(onSelectTask).toHaveBeenCalledWith(expect.objectContaining({
      id: "keju_huishi", name: "科举会试",
    }));
    fireEvent.click(view.getByRole("heading", { name: "科举乡试" }).closest("article")!.querySelector("button")!);
    expect(onSelectTask).toHaveBeenLastCalledWith(expect.objectContaining({
      id: "keju", name: "科举乡试",
    }));
  });

  it("disables huishi outside Saturday and explains the schedule", () => {
    const huishi: Task = {
      id: "keju_huishi", name: "科举会试", eyebrow: "每周六会试",
      summary: "完成会试答题", description: "仅周六开放", status: "experimental",
      risk: { level: "low", label: "低风险", summary: "仅答题" },
      steps: [], checklist: [], warnings: [], parameters: [],
      availability: {
        available: false,
        label: "仅每周六开放",
        reason: "科举会试仅在每周六开放，请在周六再运行。",
      },
      todayStatus: { status: "pending", completedAt: null, resultStatus: null },
    };
    const onSelectTask = vi.fn();
    const view = render(<TaskPanel
      system={system} tasks={[huishi]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={onSelectTask}
    />);

    const button = view.getByRole("button", {
      name: "科举会试仅在每周六开放，请在周六再运行。 →",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(view.getByText("仅每周六开放")).toBeTruthy();
    fireEvent.click(button);
    expect(onSelectTask).not.toHaveBeenCalled();
  });

  it("marks completed and pending tasks and summarizes today's completion", () => {
    const pending: Task = {
      id: "keju", name: "科举乡试", eyebrow: "每日乡试", summary: "完成乡试答题",
      description: "打开乡试正式答题页", status: "experimental",
      risk: { level: "low", label: "低风险", summary: "仅答题" },
      steps: [], checklist: [], warnings: [], parameters: [],
      todayStatus: { status: "pending", completedAt: null, resultStatus: null },
    };
    const completed: Task = {
      ...pending, id: "sanjieqiyuan", name: "三界奇缘",
      todayStatus: {
        status: "completed", completedAt: "2026-07-28T03:00:00+00:00", resultStatus: "completed",
      },
    };
    const view = render(<TaskPanel
      system={system} tasks={[completed, pending]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getByText("今日完成 1 / 2")).toBeTruthy();
    expect(view.getByText("今日已完成")).toBeTruthy();
    expect(view.getByText("今日未完成")).toBeTruthy();
    expect(view.getByRole("heading", { name: "三界奇缘" }).closest("article")?.classList.contains("completed")).toBe(true);
    expect(view.getByRole("heading", { name: "科举乡试" }).closest("article")?.classList.contains("pending")).toBe(true);
  });

  it("allows manually toggling today's completion status", () => {
    const pending: Task = {
      id: "yunbiao", name: "普通运镖", eyebrow: "每日运镖", summary: "完成普通运镖",
      description: "完成普通运镖", status: "experimental",
      risk: { level: "low", label: "低风险", summary: "普通运镖" },
      steps: [], checklist: [], warnings: [], parameters: [],
      todayStatus: { status: "pending", completedAt: null, resultStatus: null },
    };
    const completed: Task = {
      ...pending, id: "shimen", name: "师门任务",
      todayStatus: {
        status: "completed", completedAt: "2026-07-28T03:00:00+00:00",
        resultStatus: null, source: "manual",
      },
    };
    const onSetTodayStatus = vi.fn();
    const view = render(<TaskPanel
      system={system} tasks={[pending, completed]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
      onSetTodayStatus={onSetTodayStatus}
    />);

    fireEvent.click(view.getByRole("checkbox", { name: "手动修改普通运镖今日完成状态" }));
    expect(onSetTodayStatus).toHaveBeenCalledWith("yunbiao", "completed");
    fireEvent.click(view.getByRole("checkbox", { name: "手动修改师门任务今日完成状态" }));
    expect(onSetTodayStatus).toHaveBeenLastCalledWith("shimen", "pending");
  });

  it("disables completion controls while a status is being saved", () => {
    const task: Task = {
      id: "yunbiao", name: "普通运镖", eyebrow: "每日运镖", summary: "完成普通运镖",
      description: "完成普通运镖", status: "experimental",
      risk: { level: "low", label: "低风险", summary: "普通运镖" },
      steps: [], checklist: [], warnings: [], parameters: [],
      todayStatus: { status: "pending", completedAt: null, resultStatus: null },
    };
    const view = render(<TaskPanel
      system={system} tasks={[task]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy={false} progress="等待任务" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
      updatingTaskStatus="yunbiao" onSetTodayStatus={vi.fn()}
    />);

    const checkbox = view.getByRole("checkbox", {
      name: "手动修改普通运镖今日完成状态",
    }) as HTMLInputElement;
    expect(checkbox.disabled).toBe(true);
    expect(view.getByText("保存中")).toBeTruthy();
  });

  it("does not let an active run override a completed-today status", () => {
    const task: Task = {
      id: "sanjieqiyuan", name: "三界奇缘", eyebrow: "每日答题", summary: "完成答题",
      description: "完成答题", status: "experimental",
      risk: { level: "low", label: "低风险", summary: "仅答题" },
      steps: [], checklist: [], warnings: [], parameters: [],
      todayStatus: { status: "completed", completedAt: "2026-07-28T03:00:00+00:00", resultStatus: "completed" },
    };
    const activeSystem = { ...system, run: run({ status: "running" }) };
    const view = render(<TaskPanel
      system={activeSystem} tasks={[task]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy progress="自动化运行中" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getByText("今日完成 1 / 1")).toBeTruthy();
    expect(view.getByText("今日已完成")).toBeTruthy();
    expect(view.getByText("进行中")).toBeTruthy();
  });

  it("marks the active pending task as running", () => {
    const task: Task = {
      id: "sanjieqiyuan", name: "三界奇缘", eyebrow: "每日答题", summary: "完成答题",
      description: "完成答题", status: "experimental",
      risk: { level: "low", label: "低风险", summary: "仅答题" },
      steps: [], checklist: [], warnings: [], parameters: [],
      todayStatus: { status: "pending", completedAt: null, resultStatus: null },
    };
    const activeSystem = { ...system, run: run({ status: "running" }) };
    const view = render(<TaskPanel
      system={activeSystem} tasks={[task]} runs={[]}
      summary={{ total: 0, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }}
      logs={[]} busy progress="自动化运行中" startingGame={false}
      onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()}
    />);

    expect(view.getByText("今日完成 0 / 1")).toBeTruthy();
    expect(view.getByText("今日未完成")).toBeTruthy();
    expect(view.getByText("进行中")).toBeTruthy();
    expect(view.getByRole("heading", { level: 3, name: "三界奇缘" }).closest("article")?.classList.contains("running")).toBe(true);
  });

  it("shows persisted completion progress instead of a benefit placeholder", () => {
    const view = render(<TaskPanel system={system} tasks={[]} runs={[run({
      result: { task_id: "sanjieqiyuan", status: "limit_reached", questions: 10, clicks: 10, limit: 10 }
    })]} summary={{ total: 1, succeeded: 1, failed: 0, successRate: 100, averageDurationMs: 1000 }} logs={[]} busy={false} progress="等待任务" startingGame={false} onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()} />);

    expect(view.getByText("达到本次上限")).toBeTruthy();
    expect(view.getByText("已回答 10 题 · 10 次选择")).toBeTruthy();
    expect(view.queryByText("未采集收益")).toBeNull();
  });

  it("shows an automatic stop without implying the game returned safely", () => {
    const view = render(<TaskPanel system={system} tasks={[]} runs={[run({
      status: "failed", error: "未识别到明确完成状态",
      result: { task_id: "sanjieqiyuan", status: "safe_stopped", reason: "未识别到明确完成状态" }
    })]} summary={{ total: 1, succeeded: 0, failed: 1, successRate: 0, averageDurationMs: 1000 }} logs={[]} busy={false} progress="等待任务" startingGame={false} onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()} />);

    expect(view.getByText("已停止")).toBeTruthy();
    expect(view.getByText("未识别到明确完成状态")).toBeTruthy();
    expect(view.queryByText("已确认回到主界面")).toBeNull();
  });

  it("formats question and round progress for other tasks", () => {
    const runs = [
      run({
        id: "keju", taskId: "keju", taskName: "科举乡试",
        result: { task_id: "keju", status: "limit_reached", answers: 20, clicks: 20, limit: 20 }
      }),
      run({
        id: "zhuogui", taskId: "zhuogui", taskName: "捉鬼任务",
        result: { task_id: "zhuogui", status: "completed", rounds: 2, target_rounds: 2 }
      })
    ];
    const view = render(<TaskPanel system={system} tasks={[]} runs={runs} summary={{ total: 2, succeeded: 2, failed: 0, successRate: 100, averageDurationMs: 1000 }} logs={[]} busy={false} progress="等待任务" startingGame={false} onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()} />);

    expect(view.getByText("已回答 20 题 · 20 次选择")).toBeTruthy();
    expect(view.getByText("已完成 2/2 轮")).toBeTruthy();
  });

  it("does not label active runs as completed", () => {
    const runs = [
      run({ id: "preparing", status: "preparing" }),
      run({ id: "running", status: "running" }),
      run({ id: "stopping", status: "stopping" }),
    ];
    const view = render(<TaskPanel system={system} tasks={[]} runs={runs} summary={{ total: 3, succeeded: 0, failed: 0, successRate: 0, averageDurationMs: 0 }} logs={[]} busy={true} progress="自动化运行中" startingGame={false} onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()} />);

    expect(view.getByText("准备中")).toBeTruthy();
    expect(view.container.querySelector(".run-outcome.running")?.textContent).toBe("运行中");
    expect(view.getByText("正在停止")).toBeTruthy();
    expect(view.queryByText("已完成")).toBeNull();
  });

  it("describes sign-in actions and clicks accurately", () => {
    const view = render(<TaskPanel system={system} tasks={[]} runs={[run({
      taskId: "fuli_qiandao", taskName: "福利签到",
      result: { task_id: "fuli_qiandao", status: "completed", actions: 7, clicks: 2 }
    })]} summary={{ total: 1, succeeded: 1, failed: 0, successRate: 100, averageDurationMs: 1000 }} logs={[]} busy={false} progress="等待任务" startingGame={false} onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()} />);

    expect(view.getByText("已执行 7 次自动化操作 · 2 次点击")).toBeTruthy();
  });
});
