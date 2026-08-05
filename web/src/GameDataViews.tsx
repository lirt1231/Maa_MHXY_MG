import type { DailyTaskItem, GameDataSnapshot } from "./types";

function formatNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("zh-CN").format(value)
    : "—";
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return date.toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  });
}

function displayValue(value: unknown) {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "—";
}

function taskProgress(task: DailyTaskItem) {
  if (task.completed == null || task.target == null) return "—";
  return `${task.completed}/${task.target}`;
}

const TASK_STATUS_LABELS: Record<DailyTaskItem["status"], string> = {
  completed: "已完成",
  available: "可参与",
  locked: "未开放",
  unknown: "待确认",
};

const REWARD_STATUS_LABELS: Record<DailyTaskItem["rewardStatus"], string> = {
  claimable: "可领取",
  claimed: "已领取",
  unknown: "待确认",
};

function SnapshotMeta({ snapshot }: { snapshot: GameDataSnapshot }) {
  const status = {
    success: { label: "已更新", className: "succeeded" },
    partial: { label: "部分内容待确认", className: "partial" },
    failed: { label: "更新失败", className: "failed" },
  }[snapshot.status];
  return <div className="raw-data-meta">
    <span>更新于 <time dateTime={snapshot.capturedAt}>{formatTime(snapshot.capturedAt)}</time></span>
    <span className={`raw-data-status ${status.className}`}>{status.label}</span>
  </div>;
}

export function DataUpdateError({
  title, guidance, fallback, technicalError, retryLabel, retrying, disabled, onRetry
}: {
  title: string;
  guidance: string;
  fallback?: string;
  technicalError?: string | null;
  retryLabel: string;
  retrying: boolean;
  disabled: boolean;
  onRetry: () => void;
}) {
  return <section className="raw-data-error" role="alert">
    <strong>{title}</strong>
    <p>{guidance}</p>
    {fallback && <p>{fallback}</p>}
    <button className="button" disabled={disabled || retrying} onClick={onRetry}>
      {retrying ? "正在重新更新…" : retryLabel}
    </button>
    {technicalError && <details>
      <summary>查看详细原因</summary>
      <small className="capture-error">{technicalError}</small>
    </details>}
  </section>;
}

function LatestAttemptNotice({
  latestAttempt, usableSnapshot, subject, guidance, retryLabel, retrying, disabled, onRetry
}: {
  latestAttempt?: GameDataSnapshot | null;
  usableSnapshot?: GameDataSnapshot | null;
  subject: string;
  guidance: string;
  retryLabel: string;
  retrying: boolean;
  disabled: boolean;
  onRetry: () => void;
}) {
  if (!latestAttempt || latestAttempt.status === "success") return null;
  const fallback = usableSnapshot
    ? `现在继续显示 ${formatTime(usableSnapshot.capturedAt)} 更新的数据。`
    : "暂时没有之前的数据可以显示。";
  return <DataUpdateError
    title={latestAttempt.status === "partial" ? `${subject}没有更新完整` : `${subject}更新失败`}
    guidance={guidance}
    fallback={fallback}
    technicalError={latestAttempt.error}
    retryLabel={retryLabel}
    retrying={retrying}
    disabled={disabled}
    onRetry={onRetry}
  />;
}

export function CharacterResourcesView({
  snapshot, latestAttempt, capturing, disabled, onCapture
}: {
  snapshot?: GameDataSnapshot | null;
  latestAttempt?: GameDataSnapshot | null;
  capturing: boolean;
  disabled: boolean;
  onCapture: () => void;
}) {
  const resources = [
    ["金币", snapshot ? formatNumber(snapshot.data.gold) : "—"],
    ["角色等级", snapshot ? formatNumber(snapshot.data.level) : "—"],
    ["当前经验", snapshot ? formatNumber(snapshot.data.experience) : "—"],
    ["升级还需经验", snapshot ? formatNumber(snapshot.data.experienceRequired) : "—"],
  ] as const;

  return <section className="raw-data-section" role="tabpanel" id="data-panel-resources" aria-labelledby="data-tab-resources">
    <div className="panel-heading">
      <div><span className="eyebrow">当前数据</span><h2>角色资源</h2></div>
      {snapshot && <SnapshotMeta snapshot={snapshot} />}
    </div>
    <LatestAttemptNotice
      latestAttempt={latestAttempt}
      usableSnapshot={snapshot}
      subject="角色数据"
      guidance="请确认游戏窗口已经打开并且没有被其他窗口遮挡，然后再试一次。"
      retryLabel="重新更新角色数据"
      retrying={capturing}
      disabled={disabled}
      onRetry={onCapture}
    />
    {snapshot ? <>
      {snapshot.error && latestAttempt?.status === "success" && <details>
        <summary>查看记录说明</summary><small className="capture-error">{snapshot.error}</small>
      </details>}
      <dl className="resource-ledger">{resources.map(([label, value]) => <div key={label}>
        <dt>{label}</dt><dd>{value}</dd>
      </div>)}</dl>
    </> : <div className="raw-data-empty">还没有角色数据，完成一次自动记录后会显示在这里。</div>}
  </section>;
}

function readDailyTasks(snapshot?: GameDataSnapshot | null): DailyTaskItem[] {
  const tasks = snapshot?.data.tasks;
  if (!Array.isArray(tasks)) return [];
  return tasks.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const value = item as Record<string, unknown>;
    if (typeof value.name !== "string" || !value.name.trim()) return [];
    const completed = typeof value.completed === "number" && Number.isInteger(value.completed) && value.completed >= 0
      ? value.completed : null;
    const target = typeof value.target === "number" && Number.isInteger(value.target) && value.target >= 0
      ? value.target : null;
    const status = value.status === "completed" || value.status === "available" || value.status === "locked"
      ? value.status : "unknown";
    const rewardStatus = value.rewardStatus === "claimable" || value.rewardStatus === "claimed"
      ? value.rewardStatus : "unknown";
    return [{
      taskId: typeof value.taskId === "string" && value.taskId.trim() ? value.taskId : null,
      name: value.name.trim(), completed, target, status, rewardStatus
    } satisfies DailyTaskItem];
  });
}

export function DailyTasksView({
  snapshot, latestAttempt, capturing, disabled, onCapture
}: {
  snapshot?: GameDataSnapshot | null;
  latestAttempt?: GameDataSnapshot | null;
  capturing: boolean;
  disabled: boolean;
  onCapture: () => void;
}) {
  const tasks = readDailyTasks(snapshot);
  const pendingTasks = latestAttempt?.status === "partial" && latestAttempt.schemaVersion >= 2
    ? readDailyTasks(latestAttempt)
    : [];
  const updateNeedsAttention = Boolean(latestAttempt && latestAttempt.status !== "success");
  return <section className="raw-data-section" role="tabpanel" id="data-panel-daily" aria-labelledby="data-tab-daily">
    <div className="panel-heading">
      <div><span className="eyebrow">游戏日程</span><h2>今日任务</h2></div>
      <div className="raw-data-actions">
        {snapshot && <SnapshotMeta snapshot={snapshot} />}
        {!updateNeedsAttention && <button className="button" disabled={disabled || capturing} onClick={onCapture}>
          {capturing ? "正在更新…" : "更新游戏日程"}
        </button>}
      </div>
    </div>
    <LatestAttemptNotice
      latestAttempt={latestAttempt}
      usableSnapshot={snapshot}
      subject="游戏日程"
      guidance="请确认游戏已经打开并停留在可操作状态，然后重新更新游戏日程。"
      retryLabel="重新更新游戏日程"
      retrying={capturing}
      disabled={disabled}
      onRetry={onCapture}
    />
    {tasks.length ? <div className="daily-task-table-wrap"><table className="daily-task-table">
      <thead><tr><th>任务</th><th>进度</th><th>状态</th><th>奖励</th></tr></thead>
      <tbody>{tasks.map((task, index) => <tr key={task.taskId || `${task.name}-${index}`}>
        <td>{displayValue(task.name)}</td>
        <td>{taskProgress(task)}</td>
        <td>{TASK_STATUS_LABELS[task.status]}</td>
        <td>{REWARD_STATUS_LABELS[task.rewardStatus]}</td>
      </tr>)}</tbody>
    </table></div> : <div className="raw-data-empty">{snapshot
      ? "这次没有读到今日任务，请确认游戏日程页面可以正常打开。"
      : "还没有今日任务记录。"}</div>}
    {pendingTasks.length > 0 && <div className="daily-task-table-wrap">
      <div className="raw-data-meta">
        <span>下面是这次读到但还不能确认的内容</span>
      </div>
      <table className="daily-task-table" aria-label="最新尝试待确认任务">
        <thead><tr><th>待确认任务</th><th>进度</th><th>状态</th><th>奖励</th></tr></thead>
        <tbody>{pendingTasks.map((task, index) => <tr key={task.taskId || `${task.name}-${index}`}>
          <td>{displayValue(task.name)}</td>
          <td>{taskProgress(task)}</td>
          <td>待确认</td>
          <td>待确认</td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>;
}
