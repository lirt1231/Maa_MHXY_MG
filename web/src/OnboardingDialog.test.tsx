import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OnboardingDialog } from "./OnboardingDialog";
import type { SystemState, TaskInspection } from "./types";

const baseSystem: SystemState = {
  platform: "darwin", gameRunning: false, windowFound: false,
  screenCapturePermission: false, accessibilityPermission: false,
  available: false, status: "idle", metricCollectedToday: true,
};

const inspection: TaskInspection = {
  taskId: "sanjieqiyuan", status: "ready", canStart: true,
  scene: "click_activity", sceneLabel: "游戏主界面",
  summary: "当前画面支持启动此任务", proposedAction: "打开活动页面",
  reason: null, evidence: { imageSize: [1020, 718], ocrItemCount: 6 },
  observedAt: "2026-08-02T00:00:00Z",
};

afterEach(cleanup);

function props(system: SystemState, overrides: Record<string, unknown> = {}) {
  return {
    system, busy: false, startingGame: false,
    onStartGame: vi.fn().mockResolvedValue(undefined),
    onRefresh: vi.fn().mockResolvedValue(undefined),
    onOpenSettings: vi.fn().mockResolvedValue(undefined),
    onInspect: vi.fn().mockResolvedValue(inspection),
    onComplete: vi.fn(), onDismiss: vi.fn(), ...overrides,
  };
}

describe("OnboardingDialog", () => {
  it("starts by connecting the game without asking for account information", async () => {
    const values = props(baseSystem);
    const view = render(<OnboardingDialog {...values} />);

    expect(view.getByRole("heading", { name: "连接梦幻西游" })).toBeTruthy();
    expect(view.getByText(/不会输入账号信息/)).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "启动并检测游戏" }));

    await waitFor(() => expect(values.onStartGame).toHaveBeenCalledOnce());
    expect(values.onRefresh).toHaveBeenCalledOnce();
  });

  it("opens the exact macOS permission page and supports rechecking", async () => {
    const system = { ...baseSystem, gameRunning: true, windowFound: true };
    const values = props(system);
    const view = render(<OnboardingDialog {...values} />);

    expect(view.getByRole("heading", { name: "允许读取游戏画面" })).toBeTruthy();
    expect(view.getByText("屏幕与系统音频录制")).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "打开屏幕录制设置" }));
    await waitFor(() => expect(values.onOpenSettings).toHaveBeenCalledWith("screen-capture"));

    fireEvent.click(view.getByRole("button", { name: "我已开启，重新检测" }));
    await waitFor(() => expect(values.onRefresh).toHaveBeenCalledOnce());
  });

  it("runs a zero-input inspection before completion", async () => {
    const system = {
      ...baseSystem, gameRunning: true, windowFound: true,
      screenCapturePermission: true, accessibilityPermission: true, available: true,
    };
    const onComplete = vi.fn();
    const values = props(system, { onComplete });
    const view = render(<OnboardingDialog {...values} />);

    expect(view.getByText("0 次点击 · 0 次键盘输入 · 只读取当前画面")).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "开始只读画面测试" }));

    expect(await view.findByText("画面测试通过")).toBeTruthy();
    expect(view.getByText("已识别：游戏主界面")).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "进入任务中心" }));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("keeps test failures actionable and technical detail collapsed", async () => {
    const system = {
      ...baseSystem, gameRunning: true, windowFound: true,
      screenCapturePermission: true, accessibilityPermission: true, available: true,
    };
    const values = props(system, { onInspect: vi.fn().mockRejectedValue(new Error("截图超时")) });
    const view = render(<OnboardingDialog {...values} />);

    fireEvent.click(view.getByRole("button", { name: "开始只读画面测试" }));
    const alert = await view.findByRole("alert");
    expect(alert.textContent).toContain("请按上面的说明检查后重试");
    const details = alert.querySelector("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("截图超时");
  });
});
