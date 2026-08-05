import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MetricCorrectionDialog } from "./MetricCorrectionDialog";

afterEach(cleanup);

describe("MetricCorrectionDialog", () => {
  it("explains a save failure and keeps technical detail collapsed", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("数据库暂时不可写"));
    const view = render(<MetricCorrectionDialog
      snapshot={{
        id: 1, capturedAt: "2026-08-02T00:00:00Z", gold: 100, level: 69,
        experience: 200, experienceRequired: 300, status: "success", corrected: false,
      }}
      onClose={vi.fn()}
      onSave={onSave}
    />);

    fireEvent.click(view.getByRole("button", { name: "保存修正" }));
    const alert = await view.findByRole("alert");
    expect(alert.textContent).toContain("修正记录没有保存");
    expect(alert.textContent).toContain("请检查填写的数值是否正确");
    const details = alert.querySelector("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("数据库暂时不可写");

    fireEvent.click(view.getByRole("button", { name: "重新保存" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
  });
});
