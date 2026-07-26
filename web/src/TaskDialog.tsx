import { useMemo, useRef, useState } from "react";
import { InspectionEvidence } from "./InspectionEvidence";
import type { Task, TaskInspection } from "./types";

export function TaskDialog({ task, busy, onClose, onStart, onInspect }: {
  task: Task;
  busy: boolean;
  onClose: () => void;
  onStart: (options: Record<string, unknown>) => Promise<void>;
  onInspect: (options: Record<string, unknown>) => Promise<TaskInspection>;
}) {
  const defaults = useMemo(() => Object.fromEntries(task.parameters.map((item) => [item.key, item.default])), [task]);
  const [options, setOptions] = useState<Record<string, unknown>>(defaults);
  const [checks, setChecks] = useState<boolean[]>(task.checklist.map(() => false));
  const [submitting, setSubmitting] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const [inspection, setInspection] = useState<TaskInspection>();
  const [error, setError] = useState("");
  const inspectionRequest = useRef(0);
  const activeInspection = useRef(0);
  const ready = checks.every(Boolean) && !busy && !submitting && !inspecting;

  function updateOption(key: string, value: unknown) {
    inspectionRequest.current += 1;
    setOptions((current) => ({ ...current, [key]: value }));
    setInspection(undefined);
    setError("");
  }

  async function inspect() {
    const request = ++inspectionRequest.current;
    activeInspection.current = request;
    setInspecting(true); setInspection(undefined); setError("");
    try {
      const result = await onInspect(options);
      if (inspectionRequest.current === request) setInspection(result);
    } catch (reason) {
      if (inspectionRequest.current === request) {
        setError(reason instanceof Error ? reason.message : "画面检测失败");
      }
    } finally {
      if (activeInspection.current === request) setInspecting(false);
    }
  }

  async function submit() {
    setSubmitting(true); setError("");
    try { await onStart(options); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "启动失败"); }
    finally { setSubmitting(false); }
  }

  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => !inspecting && event.target === event.currentTarget && onClose()}>
    <section className="dialog" role="dialog" aria-modal="true" aria-label={`启动${task.name}`} aria-busy={inspecting}>
      <button className="dialog-close" disabled={inspecting} onClick={onClose} aria-label="关闭">×</button>
      <span className="eyebrow">{task.eyebrow}</span>
      <h2>启动 {task.name}</h2>
      <p className="dialog-intro">{task.description}</p>
      <div className={`dialog-risk ${task.risk.level}`}><strong>{task.risk.label}</strong><span>{task.risk.summary}</span></div>

      <div className="dialog-grid">
        <div>
          <h3>操作步骤</h3>
          <ol className="steps">{task.steps.map((step) => <li key={step}>{step}</li>)}</ol>
          <h3>运行参数</h3>
          <div className="parameter-list">
            {task.parameters.map((parameter) => <label className="field" key={parameter.key}>
              <span>{parameter.label}</span>
              {parameter.type === "select" ? <select value={String(options[parameter.key])} onChange={(event) => updateOption(parameter.key, event.target.value)}>
                {parameter.choices?.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select> : parameter.type === "boolean" ? <button type="button" role="switch" aria-checked={Boolean(options[parameter.key])} className={`switch ${options[parameter.key] ? "on" : ""}`} onClick={() => updateOption(parameter.key, !options[parameter.key])}>
                <span />{options[parameter.key] ? "开启" : "关闭"}
              </button> : <input type="number" min={parameter.minimum} max={parameter.maximum} value={Number(options[parameter.key])} onChange={(event) => updateOption(parameter.key, Number(event.target.value))} />}
            </label>)}
          </div>
        </div>
        <div className="check-panel">
          <h3>启动前检查</h3>
          {task.checklist.map((item, index) => <label className="check" key={item}>
            <input type="checkbox" checked={checks[index]} onChange={(event) => setChecks(checks.map((value, i) => i === index ? event.target.checked : value))} />
            <span>{item}</span>
          </label>)}
          <div className="inspection-panel">
            <button className="button ghost wide" disabled={busy || inspecting || submitting} onClick={inspect}>{inspecting ? "正在检测当前画面…" : "检测当前画面"}</button>
            {inspection && <div className={`inspection-result ${inspection.status}`} role="status" aria-live="polite">
              <div><strong>{inspection.canStart ? "可以启动" : "需要调整"}</strong><span>{inspection.sceneLabel}</span></div>
              <p>{inspection.summary}</p>
              {inspection.proposedAction && <small>下一步：{inspection.proposedAction}</small>}
              {inspection.reason && <small>原因：{inspection.reason}</small>}
              <InspectionEvidence inspection={inspection} />
            </div>}
          </div>
          <div className="warning-box">{task.warnings.map((warning) => <p key={warning}>⚠ {warning}</p>)}</div>
        </div>
      </div>
      {error && <p className="error-banner" role="alert">{error}</p>}
      <div className="dialog-actions">
        <button className="button ghost" disabled={inspecting} onClick={onClose}>取消</button>
        <button className="button primary" disabled={!ready} onClick={submit}>{busy ? "已有任务运行中" : submitting ? "正在启动…" : "确认并启动"}</button>
      </div>
    </section>
  </div>;
}
