import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PanelNavigation } from "./PanelNavigation";

describe("PanelNavigation", () => {
  it("keeps task and data panels as separate destinations", () => {
    const onChange = vi.fn();
    const view = render(<PanelNavigation active="tasks" needsCapture onChange={onChange} />);
    expect(view.getByText("任务中心").getAttribute("aria-current")).toBe("page");
    expect(view.getByTitle("今天还没有记录金币和经验")).toBeTruthy();
    fireEvent.click(view.getByText("我的收益"));
    expect(onChange).toHaveBeenCalledWith("data");
  });
});
