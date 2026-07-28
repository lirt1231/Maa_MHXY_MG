import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TaskPanel } from "./TaskPanel";
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

afterEach(cleanup);

describe("TaskPanel run outcomes", () => {
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

  it("shows the exact reason for a safe stop", () => {
    const view = render(<TaskPanel system={system} tasks={[]} runs={[run({
      status: "failed", error: "未识别到明确完成状态",
      result: { task_id: "sanjieqiyuan", status: "safe_stopped", reason: "未识别到明确完成状态" }
    })]} summary={{ total: 1, succeeded: 0, failed: 1, successRate: 0, averageDurationMs: 1000 }} logs={[]} busy={false} progress="等待任务" startingGame={false} onStartGame={vi.fn()} onStop={vi.fn()} onSelectTask={vi.fn()} />);

    expect(view.getByText("安全停止")).toBeTruthy();
    expect(view.getByText("未识别到明确完成状态")).toBeTruthy();
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
