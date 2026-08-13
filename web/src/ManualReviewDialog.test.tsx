import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ManualReviewDialog } from "./ManualReviewDialog";

afterEach(cleanup);

describe("ManualReviewDialog", () => {
  it("shows model and search material and submits the selected answer", async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<ManualReviewDialog review={{
      requestId: "request-1", question: "哪项正确？",
      options: { A: "甲", B: "乙", C: "丙", D: "丁" },
      review: {
        model: { answer: "B", confidence: 0.55, reason: "模型依据" },
        webSearch: { query: "哪项正确", evidence: [{ title: "搜索来源", snippet: "搜索摘要", url: "https://example.test" }] },
      },
    }} onSubmit={submit} />);

    expect(screen.getByText("模型依据")).toBeTruthy();
    expect(screen.getByText("搜索摘要")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "B 乙" }));
    fireEvent.click(screen.getByRole("button", { name: "确认答案" }));

    await Promise.resolve();
    expect(submit).toHaveBeenCalledWith("B");
  });
});
