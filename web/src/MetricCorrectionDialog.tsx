import { useState } from "react";
import type { MetricSnapshot } from "./types";

export function MetricCorrectionDialog({ snapshot, onClose, onSave }: {
  snapshot: MetricSnapshot;
  onClose: () => void;
  onSave: (values: Record<string, number | null>) => Promise<void>;
}) {
  const [values, setValues] = useState({
    gold: snapshot.gold?.toString() || "",
    level: snapshot.level?.toString() || "",
    experience: snapshot.experience?.toString() || "",
    experienceRequired: snapshot.experienceRequired?.toString() || ""
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  async function save() {
    setSaving(true); setError(undefined);
    const payload = Object.fromEntries(
      Object.entries(values).map(([key, value]) => [key, value === "" ? null : Number(value)])
    );
    try { await onSave(payload); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setSaving(false); }
  }

  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className="dialog correction-dialog" role="dialog" aria-modal="true" aria-label="修正指标">
      <button className="dialog-close" onClick={onClose} aria-label="关闭">×</button>
      <span className="eyebrow">CORRECT OCR SNAPSHOT</span><h2>修正指标记录</h2>
      <p className="dialog-intro">只修改能够从游戏页面确认的数值。保存后记录会标记为人工修正。</p>
      <div className="correction-grid">
        {([['gold','金币'],['level','等级'],['experience','当前经验'],['experienceRequired','升级所需经验']] as const).map(([key,label]) => <label className="field" key={key}><span>{label}</span><input min={key === 'level' ? 1 : 0} type="number" value={values[key]} onChange={(event) => setValues({ ...values, [key]: event.target.value })} /></label>)}
      </div>
      {error && <section className="error-banner player-action-error" role="alert">
        <strong>修正记录没有保存</strong>
        <p>请检查填写的数值是否正确，然后重新保存。</p>
        <button className="button ghost" disabled={saving} onClick={save}>重新保存</button>
        <details><summary>查看技术详情</summary><small>{error}</small></details>
      </section>}
      <div className="dialog-actions"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={saving} onClick={save}>{saving ? "保存中…" : "保存修正"}</button></div>
    </section>
  </div>;
}
