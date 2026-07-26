import type { TaskInspection } from "./types";

function formatObservedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "未提供";
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function InspectionEvidence({ inspection }: { inspection: TaskInspection }) {
  const { imageSize, ocrItemCount, matchedTemplates = [] } = inspection.evidence;
  const imageLabel = imageSize ? `${imageSize[0]} x ${imageSize[1]}` : "未提供";

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
    <div className="inspection-evidence-wide">
      <dt>检测时间</dt>
      <dd><time dateTime={inspection.observedAt}>{formatObservedAt(inspection.observedAt)}</time></dd>
    </div>
  </dl>;
}
