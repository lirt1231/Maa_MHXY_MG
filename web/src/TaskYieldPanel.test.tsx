import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { TaskYieldPanel, taskYieldRows } from "./TaskYieldPanel";
import type { Task, TaskYieldStatistics } from "./types";

vi.mock("./TaskYieldChart", async () => {
  const actual = await vi.importActual<typeof import("./TaskYieldChart")>("./TaskYieldChart");
  return { ...actual, TaskYieldChart: () => <div data-testid="task-yield-chart" /> };
});

afterEach(cleanup);

function task(id: string, name: string): Task {
  return {
    id, name, eyebrow: "日常", summary: "", description: "", status: "ready",
    risk: { level: "low", label: "低风险", summary: "" },
    steps: [], checklist: [], warnings: [], parameters: [],
    todayStatus: { status: "pending", completedAt: null, resultStatus: null },
  };
}

const tasks = [task("baotu", "宝图任务"), task("yunbiao", "普通运镖")];
const data: TaskYieldStatistics = {
  rangeDays: 30,
  tasks: [{
    taskId: "baotu", taskName: "宝图任务", attempts: 3, completedRuns: 3,
    reliableRuns: 2, coverageRate: 66.7, latestAt: "2026-08-03T10:00:00Z",
    metrics: {
      experience: { average: 1200, total: 2400, sampleCount: 2, minimum: 1000, maximum: 1400 },
      gold: { average: 150, total: 300, sampleCount: 2, minimum: 100, maximum: 200 },
      silver: { average: 60000, total: 120000, sampleCount: 2, minimum: 50000, maximum: 70000 },
    },
    points: [{
      runId: "run-1", completedAt: "2026-08-03T10:00:00Z",
      attributionStatus: "reliable", experienceDelta: 1200, goldDelta: 150,
      silverDelta: 60000, validMetrics: ["experience", "gold", "silver"],
    }],
  }],
};

test("comparison includes tasks without data and sorts by the selected metric", () => {
  const rows = taskYieldRows(tasks, data, "silver");
  expect(rows.map((row) => row.taskId)).toEqual(["baotu", "yunbiao"]);
  expect(rows[1].metrics.silver.sampleCount).toBe(0);
});

test("shows averages, sample quality, and a selected task trend", () => {
  const onRangeDaysChange = vi.fn();
  render(<TaskYieldPanel data={data} tasks={tasks} rangeDays={30} onRangeDaysChange={onRangeDaysChange} />);

  expect(screen.getByText("每次完整完成的平均收益")).toBeTruthy();
  expect(screen.getByText("+60,000")).toBeTruthy();
  expect(screen.getByText("样本较少")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /宝图任务\s*查看趋势/ }));
  expect(screen.getByText("平均每次完成")).toBeTruthy();
  expect(screen.getByText("66.7%")).toBeTruthy();
  expect(screen.getByTestId("task-yield-chart")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "近 90 天" }));
  expect(onRangeDaysChange).toHaveBeenCalledWith(90);
});
