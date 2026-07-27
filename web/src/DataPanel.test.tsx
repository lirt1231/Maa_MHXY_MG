import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DataPanel, snapshotsToCsv } from "./DataPanel";
import type { GameDataResponse, Metrics, SystemState } from "./types";

vi.mock("./TrendChart", () => ({
  TrendChart: () => <div data-testid="trend-chart" />
}));

afterEach(cleanup);

const system: SystemState = {
  platform: "darwin",
  gameRunning: true,
  windowFound: true,
  screenCapturePermission: true,
  accessibilityPermission: true,
  available: true,
  status: "idle",
  metricCollectedToday: true
};

const metrics: Metrics = {
  metric: "gold",
  rangeDays: 7,
  current: 160,
  change: 60,
  stats: {
    attempts: 3,
    successfulCaptures: 2,
    failedCaptures: 1,
    correctedCaptures: 1,
    captureSuccessRate: 66.7,
    validValues: 2,
    minimum: 100,
    maximum: 160,
    average: 130,
    latestChange: 60,
    averageChangePerCapture: 60,
    daysWithData: 2
  },
  points: [],
  snapshots: [
    { id: 1, capturedAt: "2026-07-24T08:00:00+08:00", gold: 100, level: 69, experience: 1000, experienceRequired: 2000, status: "success", corrected: false },
    { id: 2, capturedAt: "2026-07-25T08:00:00+08:00", gold: null, level: null, experience: null, experienceRequired: null, status: "failed", error: "OCR failed", corrected: false },
    { id: 3, capturedAt: "2026-07-26T08:00:00+08:00", gold: 160, level: 69, experience: 1200, experienceRequired: 2000, status: "success", corrected: true }
  ]
};

const gameSnapshotMeta = {
  gameDate: "2026-07-26",
  schemaVersion: 2,
  targetId: "local-primary",
  stale: false,
};

const gameData: GameDataResponse = {
  targetId: "local-primary",
  items: [
    {
      ...gameSnapshotMeta,
      id: 2, capturedAt: "2026-07-26T09:00:00+08:00", category: "daily_tasks",
      sourcePage: "活动面板", status: "success",
      data: { tasks: [
        { taskId: "daily-shimen", name: "师门任务", completed: 10, target: 20, status: "available", rewardStatus: "claimable" },
        { taskId: "daily-baotu", name: "宝图任务", completed: 10, target: 10, status: "completed", rewardStatus: "claimed" }
      ] }, evidence: {}
    },
    {
      ...gameSnapshotMeta,
      id: 1, capturedAt: "2026-07-26T08:00:00+08:00", category: "character_resources",
      sourcePage: "角色与背包", status: "success",
      data: { gold: 8888, level: 69, experience: 1200, experienceRequired: 2000 },
      evidence: { metricSnapshotId: 3, capturedFields: ["gold", "level"] }
    }
  ],
  latestByCategory: {
    character_resources: null,
    daily_tasks: null
  },
  latestAttemptByCategory: {
    character_resources: null,
    daily_tasks: null
  },
  latestUsableByCategory: {
    character_resources: null,
    daily_tasks: null
  }
};
gameData.latestByCategory.character_resources = gameData.items[1];
gameData.latestByCategory.daily_tasks = gameData.items[0];
gameData.latestAttemptByCategory.character_resources = gameData.items[1];
gameData.latestAttemptByCategory.daily_tasks = gameData.items[0];
gameData.latestUsableByCategory.character_resources = gameData.items[1];
gameData.latestUsableByCategory.daily_tasks = gameData.items[0];

const panelProps = {
  system, metrics, gameData, metricKind: "gold" as const, rangeDays: 7 as const,
  busy: false, capturing: false, capturingDailyTasks: false,
  onMetricKindChange: vi.fn(), onRangeDaysChange: vi.fn(), onCapture: vi.fn(),
  onCaptureDailyTasks: vi.fn(), onCorrect: vi.fn()
};

test("defaults to raw character resources with pending fields", () => {
  render(<DataPanel {...panelProps} />);

  expect(screen.getByRole("tab", { name: "角色资源" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByText("8,888")).toBeTruthy();
  expect(screen.getByText("1,200")).toBeTruthy();
  expect(screen.getAllByText("待接入")).toHaveLength(4);
  expect(screen.queryByTestId("trend-chart")).toBeNull();
});

test("shows daily task rows and a clear empty state", () => {
  const view = render(<DataPanel {...panelProps} />);
  fireEvent.click(screen.getByRole("tab", { name: "今日任务" }));

  expect(screen.getByText("师门任务")).toBeTruthy();
  expect(screen.getByText("10/20")).toBeTruthy();
  expect(screen.getByText("可参与")).toBeTruthy();
  expect(screen.getByText("可领取")).toBeTruthy();
  expect(screen.getByText("宝图任务")).toBeTruthy();

  view.rerender(<DataPanel {...panelProps} gameData={{
    targetId: "local-primary", items: [], latestByCategory: {},
    latestAttemptByCategory: {}, latestUsableByCategory: {}
  }} />);
  expect(screen.getByText("暂无今日任务快照。")).toBeTruthy();
});

test("requests a zero-input daily schedule capture", () => {
  const onCaptureDailyTasks = vi.fn();
  render(<DataPanel {...panelProps} onCaptureDailyTasks={onCaptureDailyTasks} />);
  fireEvent.click(screen.getByRole("tab", { name: "今日任务" }));

  fireEvent.click(screen.getByRole("button", { name: "读取当前日程" }));

  expect(onCaptureDailyTasks).toHaveBeenCalledOnce();
});

test("prevents overlapping captures and requires a fully available game", () => {
  const unavailable = { ...system, available: false };
  const view = render(<DataPanel {...panelProps} system={unavailable} capturing={true} />);
  fireEvent.click(screen.getByRole("tab", { name: "今日任务" }));
  expect((screen.getByRole("button", { name: "读取当前日程" }) as HTMLButtonElement).disabled).toBe(true);

  view.rerender(<DataPanel {...panelProps} capturingDailyTasks={true} />);
  fireEvent.click(screen.getByRole("tab", { name: "历史趋势" }));
  expect((screen.getByRole("button", { name: "立即自动采集" }) as HTMLButtonElement).disabled).toBe(true);
});

test("normalizes incomplete tasks and distinguishes partial snapshots", () => {
  const partial = {
    ...gameData.items[0], status: "partial" as const,
    data: { tasks: [{ name: "未知任务" }] }
  };
  render(<DataPanel {...panelProps} gameData={{
    targetId: "local-primary",
    items: [partial],
    latestByCategory: { daily_tasks: partial },
    latestAttemptByCategory: { daily_tasks: partial },
    latestUsableByCategory: { daily_tasks: null },
  }} />);
  fireEvent.click(screen.getByRole("tab", { name: "今日任务" }));

  expect(screen.getByText("部分识别")).toBeTruthy();
  expect(screen.getByText("未知任务")).toBeTruthy();
  expect(screen.getAllByText("待确认")).toHaveLength(2);
  expect(screen.getByText("最新尝试待确认，不可用于任务编排")).toBeTruthy();
});

test("keeps the latest usable resources after a failed attempt", () => {
  const failedResource = {
    ...gameSnapshotMeta,
    id: 3,
    capturedAt: "2026-07-26T10:00:00+08:00",
    category: "character_resources",
    sourcePage: "角色与背包",
    status: "failed" as const,
    data: {},
    evidence: {},
    error: "未识别到背包金币",
  };
  render(<DataPanel {...panelProps} gameData={{
    targetId: "local-primary",
    items: [...gameData.items, failedResource],
    latestByCategory: { ...gameData.latestByCategory, character_resources: failedResource },
    latestAttemptByCategory: { ...gameData.latestAttemptByCategory, character_resources: failedResource },
    latestUsableByCategory: gameData.latestUsableByCategory,
  }} />);

  expect(screen.getByText("8,888")).toBeTruthy();
  expect(screen.getByText("最近采集失败")).toBeTruthy();
  expect(screen.getByText("采集失败")).toBeTruthy();
  expect(screen.getByText(/当前继续显示.*最近成功数据/)).toBeTruthy();
  expect(screen.getByText(/未识别到背包金币/)).toBeTruthy();
});

test("keeps trusted daily tasks while showing a partial attempt separately", () => {
  const partialDaily = {
    ...gameSnapshotMeta,
    id: 4,
    capturedAt: "2026-07-26T11:00:00+08:00",
    category: "daily_tasks",
    sourcePage: "活动面板",
    status: "partial" as const,
    data: { tasks: [{ name: "疑似活动", completed: 1, target: 3 }] },
    evidence: {},
    error: "页面证据不完整",
  };
  render(<DataPanel {...panelProps} gameData={{
    targetId: "local-primary",
    items: [...gameData.items, partialDaily],
    latestByCategory: { ...gameData.latestByCategory, daily_tasks: partialDaily },
    latestAttemptByCategory: { ...gameData.latestAttemptByCategory, daily_tasks: partialDaily },
    latestUsableByCategory: gameData.latestUsableByCategory,
  }} />);
  fireEvent.click(screen.getByRole("tab", { name: "今日任务" }));

  expect(screen.getByText("师门任务")).toBeTruthy();
  expect(screen.getByText("宝图任务")).toBeTruthy();
  expect(screen.getByText("疑似活动")).toBeTruthy();
  expect(screen.getByText("最新尝试待确认，不可用于任务编排")).toBeTruthy();
  expect(screen.getByText(/当前继续显示.*最近成功数据/)).toBeTruthy();
  expect(screen.getByText("最近采集部分识别")).toBeTruthy();
  expect(screen.getAllByText("待确认")).toHaveLength(2);
});

test("keeps the latest usable daily tasks after a failed attempt", () => {
  const failedDaily = {
    ...gameSnapshotMeta,
    id: 5,
    capturedAt: "2026-07-26T12:00:00+08:00",
    category: "daily_tasks",
    sourcePage: "活动面板",
    status: "failed" as const,
    data: {},
    evidence: {},
    error: "未确认日程页面",
  };
  render(<DataPanel {...panelProps} gameData={{
    targetId: "local-primary",
    items: [...gameData.items, failedDaily],
    latestByCategory: { ...gameData.latestByCategory, daily_tasks: failedDaily },
    latestAttemptByCategory: { ...gameData.latestAttemptByCategory, daily_tasks: failedDaily },
    latestUsableByCategory: gameData.latestUsableByCategory,
  }} />);
  fireEvent.click(screen.getByRole("tab", { name: "今日任务" }));

  expect(screen.getByText("师门任务")).toBeTruthy();
  expect(screen.getByText("宝图任务")).toBeTruthy();
  expect(screen.getByText(/未确认日程页面/)).toBeTruthy();
  expect(screen.queryByRole("table", { name: "最新尝试待确认任务" })).toBeNull();
});

test("keeps existing analytics under the history tab", () => {
  render(<DataPanel {...panelProps} />);
  fireEvent.click(screen.getByRole("tab", { name: "历史趋势" }));

  expect(screen.getByText("66.7%")).toBeTruthy();
  expect(screen.getByText("最近采集记录")).toBeTruthy();
  expect(screen.getByText("已修正")).toBeTruthy();
  expect(screen.getByText("失败")).toBeTruthy();
  expect(screen.getByText("均值 130")).toBeTruthy();
});

test("requests a 30 day range from the range control", () => {
  const onRangeDaysChange = vi.fn();
  render(<DataPanel {...panelProps} onRangeDaysChange={onRangeDaysChange} />);

  fireEvent.click(screen.getByRole("tab", { name: "历史趋势" }));
  fireEvent.click(screen.getByRole("button", { name: "近 30 天" }));
  expect(onRangeDaysChange).toHaveBeenCalledWith(30);
});

test("exports source fields with empty values and escaped errors", () => {
  const csv = snapshotsToCsv([
    metrics.snapshots[0],
    { ...metrics.snapshots[1], error: 'OCR 失败，包含"引号"' }
  ]);

  expect(csv).toContain('"采集时间","状态","金币","等级","当前经验","升级所需经验","人工修正","错误原因"');
  expect(csv).toContain('"2026-07-25T08:00:00+08:00","失败","","","","","否","OCR 失败，包含""引号"""');
});

test("shows the export command when snapshots exist", () => {
  render(<DataPanel {...panelProps} />);

  fireEvent.click(screen.getByRole("tab", { name: "历史趋势" }));
  expect(screen.getByRole("button", { name: "导出 CSV" })).toBeTruthy();
});
