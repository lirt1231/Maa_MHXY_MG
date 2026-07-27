import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TaskDialog } from "./TaskDialog";
import type { Task, TaskInspection } from "./types";

const task: Task = {
  id: "keju",
  name: "科举乡试",
  eyebrow: "乡试 / 会试",
  summary: "答题",
  description: "请先打开正式答题页",
  status: "experimental",
  risk: { level: "low", label: "低风险", summary: "仅答题" },
  steps: ["打开答题页"],
  checklist: ["窗口完整可见", "题目与选项可见"],
  warnings: ["低置信度时停止"],
  parameters: [{ key: "maxSteps", label: "最多答题数", type: "integer", default: 20, minimum: 1, maximum: 30 }]
};

const readyInspection: TaskInspection = {
  taskId: "keju",
  status: "ready",
  canStart: true,
  scene: "formal_question_ready",
  sceneLabel: "科举正式题目页",
  summary: "当前画面支持启动此任务",
  proposedAction: "识别题目与选项后选择可靠答案",
  reason: null,
  evidence: { imageSize: [1020, 718], ocrItemCount: 8, matchedTemplates: [] },
  observedAt: "2026-07-26T00:00:00+00:00"
};

afterEach(cleanup);

describe("TaskDialog", () => {
  it("keeps the huishi dialog independent from the xiangshi task", () => {
    const huishi: Task = {
      ...task,
      id: "keju_huishi",
      name: "科举会试",
      eyebrow: "限时会试",
      description: "启动前必须手动打开科举会试正式答题页。",
      steps: ["打开科举会试正式答题页"],
      checklist: ["会试题目与四个选项可见"],
    };
    const view = render(<TaskDialog
      task={huishi} busy={false} onClose={vi.fn()} onStart={vi.fn()} onInspect={vi.fn()}
    />);

    expect(view.getByRole("dialog", { name: "启动科举会试" })).toBeTruthy();
    expect(view.getByText("启动前必须手动打开科举会试正式答题页。")).toBeTruthy();
    expect(view.getByText("会试题目与四个选项可见")).toBeTruthy();
    expect(view.queryByText("启动 科举乡试")).toBeNull();
  });

  it("requires every preflight check before enabling start", () => {
    const view = render(<TaskDialog task={task} busy={false} onClose={() => undefined} onStart={vi.fn()} onInspect={vi.fn()} />);
    const start = view.getByText("确认并启动") as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    fireEvent.click(view.getByLabelText("全选启动前检查"));
    expect(start.disabled).toBe(false);
    expect(view.getByText("低置信度时停止", { exact: false })).toBeTruthy();
  });

  it("selects and clears every preflight check from the select-all control", () => {
    const view = render(<TaskDialog task={task} busy={false} onClose={() => undefined} onStart={vi.fn()} onInspect={vi.fn()} />);
    const selectAll = view.getByLabelText("全选启动前检查") as HTMLInputElement;
    const itemChecks = view.getAllByRole("checkbox").filter((checkbox) => checkbox !== selectAll) as HTMLInputElement[];

    fireEvent.click(selectAll);
    expect(itemChecks.every((checkbox) => checkbox.checked)).toBe(true);
    expect(view.getByText("取消全选")).toBeTruthy();

    fireEvent.click(selectAll);
    expect(itemChecks.every((checkbox) => !checkbox.checked)).toBe(true);
    expect((view.getByText("确认并启动") as HTMLButtonElement).disabled).toBe(true);
    expect(view.getByText("全选")).toBeTruthy();
  });

  it("keeps the select-all state in sync with individual checks", () => {
    const view = render(<TaskDialog task={task} busy={false} onClose={() => undefined} onStart={vi.fn()} onInspect={vi.fn()} />);
    const selectAll = view.getByLabelText("全选启动前检查") as HTMLInputElement;
    const itemChecks = view.getAllByRole("checkbox").filter((checkbox) => checkbox !== selectAll);

    fireEvent.click(itemChecks[0]);
    expect(selectAll.checked).toBe(false);
    expect(selectAll.indeterminate).toBe(true);

    fireEvent.click(itemChecks[1]);
    expect(selectAll.checked).toBe(true);
    expect(selectAll.indeterminate).toBe(false);
    expect(view.getByText("取消全选")).toBeTruthy();

    fireEvent.click(itemChecks[0]);
    expect(selectAll.checked).toBe(false);
    expect(selectAll.indeterminate).toBe(true);
  });

  it("shows the recognized scene and proposed next action", async () => {
    const onInspect = vi.fn().mockResolvedValue(readyInspection);
    const view = render(<TaskDialog task={task} busy={false} onClose={() => undefined} onStart={vi.fn()} onInspect={onInspect} />);

    fireEvent.click(view.getByText("检测当前画面"));

    expect(onInspect).toHaveBeenCalledWith({ maxSteps: 20 });
    expect(await view.findByText("可以启动")).toBeTruthy();
    expect(view.getByText("科举正式题目页")).toBeTruthy();
    expect(view.getByText("下一步：识别题目与选项后选择可靠答案")).toBeTruthy();
    expect(view.getByLabelText("检测证据")).toBeTruthy();
    expect(view.getByText("1020 x 718")).toBeTruthy();
    expect(view.getByText("8 条")).toBeTruthy();
  });

  it("shows unsupported reasons without pretending the scene is ready", async () => {
    const onInspect = vi.fn().mockResolvedValue({
      ...readyInspection,
      status: "unsupported",
      canStart: false,
      scene: "waiting_for_question",
      sceneLabel: "科举等待页",
      summary: "科举尚未进入正式题目",
      proposedAction: null,
      reason: "等待正式题目出现后重新检测"
    });
    const view = render(<TaskDialog task={task} busy={false} onClose={() => undefined} onStart={vi.fn()} onInspect={onInspect} />);

    fireEvent.click(view.getByText("检测当前画面"));

    expect(await view.findByText("需要调整")).toBeTruthy();
    expect(view.getByText("原因：等待正式题目出现后重新检测")).toBeTruthy();
  });

  it("invalidates an in-flight result when parameters change", async () => {
    let resolveInspection: (value: TaskInspection) => void = () => undefined;
    const onInspect = vi.fn(() => new Promise<TaskInspection>((resolve) => { resolveInspection = resolve; }));
    const view = render(<TaskDialog task={task} busy={false} onClose={() => undefined} onStart={vi.fn()} onInspect={onInspect} />);

    fireEvent.click(view.getByText("检测当前画面"));
    expect(view.getByText("正在检测当前画面…")).toBeTruthy();
    fireEvent.click(view.getByLabelText("全选启动前检查"));
    fireEvent.change(view.getByRole("spinbutton"), { target: { value: "15" } });
    expect((view.getByText("确认并启动") as HTMLButtonElement).disabled).toBe(true);
    await act(async () => resolveInspection(readyInspection));

    await waitFor(() => expect(view.getByText("检测当前画面")).toBeTruthy());
    expect(view.queryByText("可以启动")).toBeNull();
  });

  it("keeps inspection errors in the dialog and disables actions while busy", async () => {
    const onInspect = vi.fn().mockRejectedValue(new Error("画面检测超时"));
    const view = render(<TaskDialog task={task} busy={false} onClose={() => undefined} onStart={vi.fn()} onInspect={onInspect} />);

    fireEvent.click(view.getByText("检测当前画面"));
    expect((await view.findByRole("alert")).textContent).toContain("画面检测超时");

    view.rerender(<TaskDialog task={task} busy={true} onClose={() => undefined} onStart={vi.fn()} onInspect={onInspect} />);
    expect((view.getByText("检测当前画面") as HTMLButtonElement).disabled).toBe(true);
    expect((view.getByText("已有任务运行中") as HTMLButtonElement).disabled).toBe(true);
  });
});
