import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TaskDialog } from "./TaskDialog";
import type { Task } from "./types";

const task: Task = {
  id: "keju",
  name: "科举乡试",
  eyebrow: "乡试 / 会试",
  summary: "答题",
  description: "请先打开正式答题页",
  status: "experimental",
  steps: ["打开答题页"],
  checklist: ["窗口完整可见", "题目与选项可见"],
  warnings: ["低置信度时停止"],
  parameters: [{ key: "maxSteps", label: "最多答题数", type: "integer", default: 20, minimum: 1, maximum: 30 }]
};

describe("TaskDialog", () => {
  it("requires every preflight check before enabling start", () => {
    const view = render(<TaskDialog task={task} busy={false} onClose={() => undefined} onStart={vi.fn()} />);
    const start = view.getByText("确认并启动") as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    view.getAllByRole("checkbox").forEach((checkbox) => fireEvent.click(checkbox));
    expect(start.disabled).toBe(false);
    expect(view.getByText("低置信度时停止", { exact: false })).toBeTruthy();
  });
});
