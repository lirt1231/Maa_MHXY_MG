import { useState } from "react";

export type ManualReview = {
  requestId: string;
  question: string;
  options: Partial<Record<"A" | "B" | "C" | "D", string>>;
  review: {
    reason?: string;
    model?: {
      answer?: string;
      confidence?: number;
      reason?: string;
      error?: string;
      rawResponse?: string;
    };
    webSearch?: {
      query?: string;
      evidence?: { id?: string; title?: string; snippet?: string; url?: string; provider?: string }[];
    };
  };
};

export function ManualReviewDialog({ review, onSubmit, onExit }: {
  review: ManualReview;
  onSubmit: (answer: "A" | "B" | "C" | "D") => Promise<void>;
  onExit: () => Promise<void>;
}) {
  const [selected, setSelected] = useState<"A" | "B" | "C" | "D">();
  const [submitting, setSubmitting] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [error, setError] = useState<string>();
  const model = review.review.model;
  const evidence = review.review.webSearch?.evidence || [];

  async function submit() {
    if (!selected) return;
    setSubmitting(true); setError(undefined);
    try { await onSubmit(selected); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "提交失败"); setSubmitting(false); }
  }

  async function exit() {
    setExiting(true); setError(undefined);
    try { await onExit(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "退出失败"); setExiting(false); }
  }

  return <div className="dialog-backdrop manual-review-backdrop">
    <section className="dialog manual-review-dialog" role="dialog" aria-modal="true" aria-label="乡试人工复核">
      <span className="eyebrow">乡试人工复核</span>
      <h2>请选择本题答案</h2>
      <p className="manual-review-question">{review.question}</p>
      <div className="manual-review-options" role="radiogroup" aria-label="答案选项">
        {(["A", "B", "C", "D"] as const).filter((answer) => Boolean(review.options[answer])).map((answer) => <button
          key={answer} type="button" role="radio" aria-checked={selected === answer}
          className={selected === answer ? "selected" : ""} disabled={submitting || exiting}
          onClick={() => setSelected(answer)}><b>{answer}</b><span>{review.options[answer]}</span>
        </button>)}
      </div>
      <section className="manual-review-material">
        <div><span>模型建议</span><strong>{model?.answer || "无有效建议"}{typeof model?.confidence === "number" ? ` · 置信度 ${model.confidence.toFixed(2)}` : ""}</strong></div>
        <p>{model?.reason || review.review.reason || "模型未提供可自动作答的依据。"}</p>
        {model?.error && <small>{model.error}</small>}
        {model?.rawResponse && <details><summary>模型原始回答</summary><pre>{model.rawResponse}</pre></details>}
      </section>
      <section className="manual-review-material">
        <div><span>搜索结果</span><strong>{review.review.webSearch?.query || "未返回搜索查询"}</strong></div>
        {evidence.length ? <ul>{evidence.map((item, index) => <li key={`${item.id || "evidence"}-${index}`}><strong>{item.title || item.provider || "搜索结果"}</strong><p>{item.snippet || "无摘要"}</p>{item.url && <a href={item.url} target="_blank" rel="noreferrer">打开来源</a>}</li>)}</ul> : <p>没有可用的搜索结果。</p>}
      </section>
      {error && <p className="manual-review-error" role="alert">{error}</p>}
      <div className="dialog-actions"><button className="button ghost" disabled={submitting || exiting} onClick={exit}>{exiting ? "正在退出答题…" : "退出答题"}</button><button className="button primary" disabled={!selected || submitting || exiting} onClick={submit}>{submitting ? "正在复核当前题面…" : "确认答案"}</button></div>
    </section>
  </div>;
}
