import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PanelNavigation } from "./PanelNavigation";

describe("PanelNavigation", () => {
  it("keeps task and data panels as separate destinations", () => {
    const onChange = vi.fn();
    const view = render(<PanelNavigation active="tasks" needsCapture onChange={onChange} />);
    expect(view.getByText("任务面板").getAttribute("aria-current")).toBe("page");
    expect(view.getByTitle("今天尚未采集")).toBeTruthy();
    fireEvent.click(view.getByText("数据面板"));
    expect(onChange).toHaveBeenCalledWith("data");
  });
});
