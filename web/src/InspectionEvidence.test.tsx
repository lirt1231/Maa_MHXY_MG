import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { InspectionEvidence } from "./InspectionEvidence";
import type { TaskInspection } from "./types";

const inspection: TaskInspection = {
  taskId: "fuli_qiandao",
  status: "ready",
  canStart: true,
  scene: "open_welfare",
  sceneLabel: "福利入口",
  summary: "当前画面支持启动此任务",
  proposedAction: "打开福利",
  reason: null,
  evidence: {
    imageSize: [1020, 718],
    ocrItemCount: 8,
    matchedTemplates: ["welfare_header", "scratch_button"]
  },
  observedAt: "2026-07-26T08:12:30+08:00"
};

afterEach(cleanup);

describe("InspectionEvidence", () => {
  it("shows screenshot, OCR, template and timestamp evidence", () => {
    const view = render(<InspectionEvidence inspection={inspection} />);

    expect(view.getByLabelText("检测证据")).toBeTruthy();
    expect(view.getByText("1020 x 718")).toBeTruthy();
    expect(view.getByText("8 条")).toBeTruthy();
    expect(view.getByText("welfare_header")).toBeTruthy();
    expect(view.getByText("scratch_button")).toBeTruthy();
    expect(view.container.querySelector("time")?.getAttribute("datetime")).toBe(inspection.observedAt);
  });

  it("keeps missing optional evidence explicit", () => {
    const view = render(<InspectionEvidence inspection={{
      ...inspection,
      evidence: { imageSize: null, matchedTemplates: [] },
      observedAt: ""
    }} />);

    expect(view.getAllByText("未提供")).toHaveLength(3);
    expect(view.getByText("未命中")).toBeTruthy();
  });

  it("shows read-only coordinate prediction when frame evidence exists", () => {
    const view = render(<InspectionEvidence inspection={{
      ...inspection,
      evidence: {
        ...inspection.evidence,
        frameId: "frame-1",
        captureMethod: "maa",
        windowBoundsPoints: [-1786, -106, 1051, 820],
        contentRectPoints: [-1786, -105.8483, 1051, 819.8483],
        scaleFactor: 0.8782112274,
        anchorValidation: "pending",
        recognizedBoxCenterPixels: [275, 50],
        predictedScreenPoint: [-1472.9, -48.9],
      },
    }} />);

    expect(view.getByText("maa · 0.878211x")).toBeTruthy();
    expect(view.getByText("待验证")).toBeTruthy();
    expect(view.getByText("275, 50 px")).toBeTruthy();
    expect(view.getByText("-1472.9, -48.9 pt")).toBeTruthy();
    expect(view.getByText("frame-1")).toBeTruthy();
  });
});
