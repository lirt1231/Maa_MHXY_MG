import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const apiMock = vi.hoisted(() => ({
  system: vi.fn(),
  tasks: vi.fn(),
  runs: vi.fn(),
  metrics: vi.fn(),
  gameData: vi.fn(),
  start: vi.fn(),
  inspect: vi.fn(),
  stop: vi.fn(),
  capture: vi.fn(),
  captureDailyTasks: vi.fn(),
  startGame: vi.fn(),
  correctMetric: vi.fn(),
}));

vi.mock("./api", () => ({ api: apiMock }));

class EventSourceStub {
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;

  constructor(_url: string) {}
  addEventListener() {}
  close() {}
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
    apiMock.metrics.mockResolvedValue(undefined);
    apiMock.gameData.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    vi.clearAllMocks();
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
});
