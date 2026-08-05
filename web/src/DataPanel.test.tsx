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
  busy: false, capturing: false,
  onMetricKindChange: vi.fn(), onRangeDaysChange: vi.fn(), onCapture: vi.fn(),
  onCorrect: vi.fn()
};

test("defaults to current resources and hides unavailable fields", () => {
  render(<DataPanel {...panelProps} />);

  expect(screen.getByRole("tab", { name: "当前资源" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByText("8,888")).toBeTruthy();
  expect(screen.getByText("1,200")).toBeTruthy();
  expect(screen.queryByText("待接入")).toBeNull();
  expect(screen.queryByText("银币")).toBeNull();
  expect(screen.queryByText("仙玉")).toBeNull();
  expect(screen.queryByRole("tab", { name: "游戏日程" })).toBeNull();
  expect(screen.queryByTestId("trend-chart")).toBeNull();
});

test("does not relabel stale gold or seven-day data as experience for thirty days", () => {
  render(<DataPanel
    {...panelProps}
    metricKind="experience"
    rangeDays={30}
  />);
  fireEvent.click(screen.getByRole("tab", { name: "收益走势" }));

  const currentExperience = screen.getByText("当前经验").closest("article");
  expect(currentExperience?.textContent).toContain("—");
  expect(currentExperience?.textContent).toContain("正在加载对应数据");
  expect(currentExperience?.textContent).not.toContain("160");
  expect(screen.getAllByText("近 30 天还没有足够的数据")).toHaveLength(2);
});

test("requires a fully available game before updating earnings", () => {
  const unavailable = { ...system, available: false };
  render(<DataPanel {...panelProps} system={unavailable} />);
  fireEvent.click(screen.getByRole("tab", { name: "收益走势" }));
  expect((screen.getByRole("button", { name: "立即更新数据" }) as HTMLButtonElement).disabled).toBe(true);
});

test("keeps the latest usable resources after a failed attempt", () => {
  const onCapture = vi.fn();
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
  render(<DataPanel {...panelProps} onCapture={onCapture} gameData={{
    targetId: "local-primary",
    items: [...gameData.items, failedResource],
    latestByCategory: { ...gameData.latestByCategory, character_resources: failedResource },
    latestAttemptByCategory: { ...gameData.latestAttemptByCategory, character_resources: failedResource },
    latestUsableByCategory: gameData.latestUsableByCategory,
  }} />);

  expect(screen.getByText("8,888")).toBeTruthy();
  expect(screen.getByText("这次更新失败")).toBeTruthy();
  expect(screen.getByText("角色数据更新失败")).toBeTruthy();
  expect(screen.getByText(/请确认游戏窗口已经打开.*再试一次/)).toBeTruthy();
  expect(screen.getByText(/现在继续显示.*更新的数据/)).toBeTruthy();
  expect(screen.getByText(/未识别到背包金币/)).toBeTruthy();
  const details = screen.getByText("查看详细原因").closest("details");
  expect(details?.open).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "重新更新角色数据" }));
  expect(onCapture).toHaveBeenCalledOnce();
});

test("explains a failed earnings update and reuses the existing retry action", () => {
  const onCapture = vi.fn();
  const failedMetrics: Metrics = {
    ...metrics,
    snapshots: [...metrics.snapshots, {
      id: 4,
      capturedAt: "2026-07-26T09:00:00+08:00",
      gold: null,
      level: null,
      experience: null,
      experienceRequired: null,
      status: "failed",
      corrected: false,
      error: "OCR did not find the inventory amount",
    }],
  };
  render(<DataPanel {...panelProps} metrics={failedMetrics} onCapture={onCapture} />);
  fireEvent.click(screen.getByRole("tab", { name: "收益走势" }));

  expect(screen.getByText("游戏数据更新失败")).toBeTruthy();
  expect(screen.getByText(/请确认游戏窗口已经打开.*再试一次/)).toBeTruthy();
  expect(screen.getByText("OCR did not find the inventory amount")).toBeTruthy();
  const details = screen.getByText("查看详细原因").closest("details");
  expect(details?.open).toBe(false);
  expect(screen.queryByRole("button", { name: "立即更新数据" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重新更新游戏数据" }));
  expect(onCapture).toHaveBeenCalledOnce();
});

test("prioritizes plain-language earnings and keeps technical stats collapsed", () => {
  render(<DataPanel {...panelProps} />);
  fireEvent.click(screen.getByRole("tab", { name: "收益走势" }));

  expect(screen.getAllByText("近 7 天增加了 60")).toHaveLength(2);
  expect(screen.getByText("金币变化")).toBeTruthy();
  const latestChange = screen.getByText("相比上次记录").closest("article");
  expect(latestChange?.textContent).toContain("+60");
  expect(latestChange?.textContent).toContain("比上次增加 60");
  const details = screen.getByText("查看详细数据").closest("details");
  expect(details?.open).toBe(false);
  expect(screen.getByText("66.7%")).toBeTruthy();
  expect(screen.getByText("最近的数据记录")).toBeTruthy();
  expect(screen.getByText("已修正")).toBeTruthy();
  expect(screen.getByText("失败")).toBeTruthy();
  expect(screen.getByText("平均金币")).toBeTruthy();
});

test("does not present a level-up reset as a negative experience change", () => {
  const experienceMetrics: Metrics = {
    ...metrics,
    metric: "experience",
    current: 200,
    change: -800,
    stats: { ...metrics.stats, latestChange: -800 },
    snapshots: [
      { ...metrics.snapshots[0], experience: 1000, level: 69 },
      { ...metrics.snapshots[2], experience: 200, level: 70 },
    ],
  };
  render(<DataPanel {...panelProps} metrics={experienceMetrics} metricKind="experience" />);
  fireEvent.click(screen.getByRole("tab", { name: "收益走势" }));

  const latestChange = screen.getByText("相比上次记录").closest("article");
  expect(latestChange?.textContent).toContain("—");
  expect(latestChange?.textContent).toContain("等级发生变化，暂不计算跨等级差值");
  expect(latestChange?.textContent).not.toContain("-800");
});

test("requests a 30 day range from the range control", () => {
  const onRangeDaysChange = vi.fn();
  render(<DataPanel {...panelProps} onRangeDaysChange={onRangeDaysChange} />);

  fireEvent.click(screen.getByRole("tab", { name: "收益走势" }));
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

test("keeps the export command inside advanced details", () => {
  render(<DataPanel {...panelProps} />);

  fireEvent.click(screen.getByRole("tab", { name: "收益走势" }));
  const details = screen.getByText("查看详细数据").closest("details");
  expect(details?.open).toBe(false);
  expect(screen.getByRole("button", { name: "导出 CSV" })).toBeTruthy();
});
