import type { TaskInspection } from "./types";

function formatObservedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "未提供";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatPoint(value?: number[], unit = "") {
  if (!value) return "未生成";
  return `${value.map((item) => Number(item.toFixed(3))).join(", ")}${unit}`;
}

export function InspectionEvidence({ inspection }: { inspection: TaskInspection }) {
  const {
    imageSize,
    ocrItemCount,
    matchedTemplates = [],
    frameId,
    captureMethod,
    windowBoundsPoints,
    contentRectPoints,
    scaleFactor,
    anchorValidation,
    recognizedBoxCenterPixels,
    predictedScreenPoint,
  } = inspection.evidence;
  const imageLabel = imageSize ? `${imageSize[0]} x ${imageSize[1]}` : "未提供";
  const hasCoordinateEvidence = Boolean(frameId || contentRectPoints || scaleFactor);

  return <dl className="inspection-evidence" aria-label="检测证据">
    <div>
      <dt>截图尺寸</dt>
      <dd>{imageLabel}</dd>
    </div>
    <div>
      <dt>OCR 文本</dt>
      <dd>{ocrItemCount === undefined ? "未提供" : `${ocrItemCount} 条`}</dd>
    </div>
    <div className="inspection-evidence-wide">
      <dt>命中模板</dt>
      <dd className="inspection-template-list">{matchedTemplates.length
        ? matchedTemplates.map((template) => <code key={template}>{template}</code>)
        : "未命中"}</dd>
    </div>
    {hasCoordinateEvidence && <>
      <div>
        <dt>截图映射</dt>
        <dd>{captureMethod || "未知"}{scaleFactor ? ` · ${scaleFactor.toFixed(6)}x` : ""}</dd>
      </div>
      <div>
        <dt>锚点校验</dt>
        <dd>{anchorValidation === "pending" ? "待验证" : anchorValidation || "未提供"}</dd>
      </div>
      <div className="inspection-evidence-wide">
        <dt>窗口边界</dt>
        <dd>{formatPoint(windowBoundsPoints, " pt")}</dd>
      </div>
      <div className="inspection-evidence-wide">
        <dt>内容矩形</dt>
        <dd>{formatPoint(contentRectPoints, " pt")}</dd>
      </div>
      <div>
        <dt>识别框中心</dt>
        <dd>{formatPoint(recognizedBoxCenterPixels, " px")}</dd>
      </div>
      <div>
        <dt>预测屏幕点</dt>
        <dd>{formatPoint(predictedScreenPoint, " pt")}</dd>
      </div>
      <div className="inspection-evidence-wide">
        <dt>帧 ID</dt>
        <dd><code>{frameId || "未提供"}</code></dd>
      </div>
    </>}
    <div className="inspection-evidence-wide">
      <dt>检测时间</dt>
      <dd><time dateTime={inspection.observedAt}>{formatObservedAt(inspection.observedAt)}</time></dd>
    </div>
  </dl>;
}
