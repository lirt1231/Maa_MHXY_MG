import { useMemo, useState } from "react";
import type { Task } from "./types";

export function TaskDialog({ task, busy, onClose, onStart }: {
  task: Task;
  busy: boolean;
  onClose: () => void;
  onStart: (options: Record<string, unknown>) => Promise<void>;
}) {
  const defaults = useMemo(() => Object.fromEntries(task.parameters.map((item) => [item.key, item.default])), [task]);
  const [options, setOptions] = useState<Record<string, unknown>>(defaults);
  const [checks, setChecks] = useState<boolean[]>(task.checklist.map(() => false));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const ready = checks.every(Boolean) && !busy && !submitting;

  async function submit() {
    setSubmitting(true); setError("");
    try { await onStart(options); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "启动失败"); }
    finally { setSubmitting(false); }
  }

  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className="dialog" role="dialog" aria-modal="true" aria-label={`启动${task.name}`}>
      <button className="dialog-close" onClick={onClose} aria-label="关闭">×</button>
      <span className="eyebrow">{task.eyebrow}</span>
      <h2>启动 {task.name}</h2>
      <p className="dialog-intro">{task.description}</p>

      <div className="dialog-grid">
        <div>
          <h3>操作步骤</h3>
          <ol className="steps">{task.steps.map((step) => <li key={step}>{step}</li>)}</ol>
          <h3>运行参数</h3>
          <div className="parameter-list">
            {task.parameters.map((parameter) => <label className="field" key={parameter.key}>
              <span>{parameter.label}</span>
              {parameter.type === "select" ? <select value={String(options[parameter.key])} onChange={(event) => setOptions({ ...options, [parameter.key]: event.target.value })}>
                {parameter.choices?.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select> : parameter.type === "boolean" ? <button type="button" className={`switch ${options[parameter.key] ? "on" : ""}`} onClick={() => setOptions({ ...options, [parameter.key]: !options[parameter.key] })}>
                <span />{options[parameter.key] ? "开启" : "关闭"}
              </button> : <input type="number" min={parameter.minimum} max={parameter.maximum} value={Number(options[parameter.key])} onChange={(event) => setOptions({ ...options, [parameter.key]: Number(event.target.value) })} />}
            </label>)}
          </div>
        </div>
        <div className="check-panel">
          <h3>启动前检查</h3>
          {task.checklist.map((item, index) => <label className="check" key={item}>
            <input type="checkbox" checked={checks[index]} onChange={(event) => setChecks(checks.map((value, i) => i === index ? event.target.checked : value))} />
            <span>{item}</span>
          </label>)}
          <div className="warning-box">{task.warnings.map((warning) => <p key={warning}>⚠ {warning}</p>)}</div>
        </div>
      </div>
      {error && <p className="error-banner">{error}</p>}
      <div className="dialog-actions">
        <button className="button ghost" onClick={onClose}>取消</button>
        <button className="button primary" disabled={!ready} onClick={submit}>{busy ? "已有任务运行中" : submitting ? "正在启动…" : "确认并启动"}</button>
      </div>
    </section>
  </div>;
}
