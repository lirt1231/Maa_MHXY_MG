import { useMemo, useState } from "react";
import type { SystemState, TaskInspection } from "./types";

type StepId = "game" | "screen" | "accessibility" | "window" | "test" | "done";

const STEPS: { id: Exclude<StepId, "done">; label: string }[] = [
  { id: "game", label: "连接游戏" },
  { id: "screen", label: "画面权限" },
  { id: "accessibility", label: "操作权限" },
  { id: "window", label: "检测窗口" },
  { id: "test", label: "安全测试" },
];

function currentStep(system: SystemState | undefined, tested: boolean): StepId {
  if (!system?.gameRunning) return "game";
  if (!system.screenCapturePermission) return "screen";
  if (!system.accessibilityPermission) return "accessibility";
  if (!system.windowFound) return "window";
  return tested ? "done" : "test";
}

function stepIndex(step: StepId) {
  return step === "done" ? STEPS.length : STEPS.findIndex((item) => item.id === step);
}

export function OnboardingDialog({
  system, busy, startingGame, onStartGame, onRefresh, onOpenSettings, onInspect,
  onComplete, onDismiss,
}: {
  system?: SystemState;
  busy: boolean;
  startingGame: boolean;
  onStartGame: () => Promise<void> | void;
  onRefresh: () => Promise<void>;
  onOpenSettings: (section: "screen-capture" | "accessibility") => Promise<void>;
  onInspect: () => Promise<TaskInspection>;
  onComplete: () => void;
  onDismiss: () => void;
}) {
  const [working, setWorking] = useState<string>();
  const [tested, setTested] = useState(false);
  const [inspection, setInspection] = useState<TaskInspection>();
  const [error, setError] = useState<string>();
  const step = currentStep(system, tested);
  const activeIndex = stepIndex(step);
  const disabled = Boolean(working || busy || startingGame);

  const content = useMemo(() => {
    if (step === "game") return {
      eyebrow: "第一步",
      title: "连接梦幻西游",
      description: "启动游戏并登录角色。向导只检测本机游戏窗口，不会输入账号信息。",
    };
    if (step === "screen") return {
      eyebrow: "第二步",
      title: "允许读取游戏画面",
      description: "前往“系统设置 → 隐私与安全性 → 屏幕与系统音频录制”，为运行梦手控制台的应用开启权限。",
    };
    if (step === "accessibility") return {
      eyebrow: "第三步",
      title: "允许操作游戏窗口",
      description: "前往“系统设置 → 隐私与安全性 → 辅助功能”，为运行梦手控制台的应用开启权限。",
    };
    if (step === "window") return {
      eyebrow: "第四步",
      title: "让游戏窗口保持可见",
      description: "取消最小化并关闭遮挡游戏的窗口，然后将梦幻西游切到前台。",
    };
    if (step === "test") return {
      eyebrow: "最后检查",
      title: "运行一次只读画面测试",
      description: "测试只会截取并识别当前游戏画面，不会点击、输入或消耗任何游戏资源。",
    };
    return {
      eyebrow: "设置完成",
      title: "已经可以使用自动任务",
      description: "游戏窗口、画面权限和操作权限均已通过检测。",
    };
  }, [step]);

  async function runAction(name: string, action: () => Promise<void>) {
    setWorking(name); setError(undefined);
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作没有完成"); }
    finally { setWorking(undefined); }
  }

  async function runInspection() {
    await runAction("test", async () => {
      const result = await onInspect();
      if (!result.observedAt || !result.scene) throw new Error("画面测试没有返回有效结果");
      setInspection(result);
      setTested(true);
    });
  }

  return <div className="dialog-backdrop onboarding-backdrop" role="presentation">
    <section className="dialog onboarding-dialog" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
      <button className="dialog-close" onClick={onDismiss} aria-label="稍后设置">×</button>
      <div className="onboarding-layout">
        <aside className="onboarding-steps" aria-label="首次设置进度">
          <strong>首次设置</strong>
          <ol>{STEPS.map((item, index) => <li key={item.id} className={index < activeIndex ? "done" : index === activeIndex ? "active" : "pending"}>
            <span>{index < activeIndex ? "✓" : index + 1}</span><b>{item.label}</b>
          </li>)}</ol>
          <small>整个过程不会开始任何游戏任务。</small>
        </aside>
        <div className="onboarding-content">
          <span className="eyebrow">{content.eyebrow}</span>
          <h2 id="onboarding-title">{content.title}</h2>
          <p className="dialog-intro">{content.description}</p>

          {(step === "screen" || step === "accessibility") && <div className="settings-path" aria-label="系统设置路径">
            <span></span><div><small>系统设置</small><strong>隐私与安全性</strong><b>→</b><strong>{step === "screen" ? "屏幕与系统音频录制" : "辅助功能"}</strong></div>
          </div>}

          {step === "test" && <div className="zero-input-note"><strong>本次测试不会操作游戏</strong><span>0 次点击 · 0 次键盘输入 · 只读取当前画面</span></div>}
          {step === "done" && inspection && <div className="onboarding-success" role="status"><strong>画面测试通过</strong><span>已识别：{inspection.sceneLabel}</span></div>}

          {error && <section className="error-banner player-action-error" role="alert">
            <strong>这一步还没有完成</strong><p>请按上面的说明检查后重试。</p>
            <details><summary>查看技术详情</summary><small>{error}</small></details>
          </section>}

          <div className="onboarding-actions">
            <button className="button ghost" onClick={onDismiss}>稍后设置</button>
            {step === "game" && <button className="button primary" disabled={disabled} onClick={() => runAction("game", async () => { await onStartGame(); await onRefresh(); })}>{working === "game" || startingGame ? "正在连接…" : "启动并检测游戏"}</button>}
            {step === "screen" && <><button className="button" disabled={disabled} onClick={() => runAction("settings", () => onOpenSettings("screen-capture"))}>打开屏幕录制设置</button><button className="button primary" disabled={disabled} onClick={() => runAction("refresh", onRefresh)}>{working === "refresh" ? "正在检测…" : "我已开启，重新检测"}</button></>}
            {step === "accessibility" && <><button className="button" disabled={disabled} onClick={() => runAction("settings", () => onOpenSettings("accessibility"))}>打开辅助功能设置</button><button className="button primary" disabled={disabled} onClick={() => runAction("refresh", onRefresh)}>{working === "refresh" ? "正在检测…" : "我已开启，重新检测"}</button></>}
            {step === "window" && <button className="button primary" disabled={disabled} onClick={() => runAction("window", async () => { await onStartGame(); await onRefresh(); })}>{working === "window" ? "正在检测…" : "唤起游戏并重新检测"}</button>}
            {step === "test" && <button className="button primary" disabled={disabled} onClick={runInspection}>{working === "test" ? "正在测试画面…" : "开始只读画面测试"}</button>}
            {step === "done" && <button className="button primary" onClick={onComplete}>进入任务中心</button>}
          </div>
        </div>
      </div>
    </section>
  </div>;
}
