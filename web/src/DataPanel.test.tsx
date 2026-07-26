import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DataPanel, snapshotsToCsv } from "./DataPanel";
import type { Metrics, SystemState } from "./types";

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

test("shows resource statistics and recent capture quality", () => {
  render(<DataPanel
    system={system} metrics={metrics} metricKind="gold" rangeDays={7}
    busy={false} capturing={false} onMetricKindChange={vi.fn()}
    onRangeDaysChange={vi.fn()} onCapture={vi.fn()} onCorrect={vi.fn()}
  />);

  expect(screen.getByText("66.7%")).toBeTruthy();
  expect(screen.getByText("最近采集记录")).toBeTruthy();
  expect(screen.getByText("已修正")).toBeTruthy();
  expect(screen.getByText("失败")).toBeTruthy();
  expect(screen.getByText("均值 130")).toBeTruthy();
});

test("requests a 30 day range from the range control", () => {
  const onRangeDaysChange = vi.fn();
  render(<DataPanel
    system={system} metrics={metrics} metricKind="gold" rangeDays={7}
    busy={false} capturing={false} onMetricKindChange={vi.fn()}
    onRangeDaysChange={onRangeDaysChange} onCapture={vi.fn()} onCorrect={vi.fn()}
  />);

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
  render(<DataPanel
    system={system} metrics={metrics} metricKind="gold" rangeDays={7}
    busy={false} capturing={false} onMetricKindChange={vi.fn()}
    onRangeDaysChange={vi.fn()} onCapture={vi.fn()} onCorrect={vi.fn()}
  />);

  expect(screen.getByRole("button", { name: "导出 CSV" })).toBeTruthy();
});
