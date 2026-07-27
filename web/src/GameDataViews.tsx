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
    success: { label: "采集成功", className: "succeeded" },
    partial: { label: "部分识别", className: "partial" },
    failed: { label: "采集失败", className: "failed" },
  }[snapshot.status];
  return <div className="raw-data-meta">
    <span>来源：{snapshot.sourcePage || "未知页面"}</span>
    <span>采集：<time dateTime={snapshot.capturedAt}>{formatTime(snapshot.capturedAt)}</time></span>
    <span className={`raw-data-status ${status.className}`}>{status.label}</span>
  </div>;
}

function LatestAttemptNotice({
  latestAttempt, usableSnapshot
}: {
  latestAttempt?: GameDataSnapshot | null;
  usableSnapshot?: GameDataSnapshot | null;
}) {
  if (!latestAttempt || latestAttempt.status === "success") return null;
  const status = latestAttempt.status === "partial" ? "部分识别" : "采集失败";
  const fallback = usableSnapshot
    ? `当前继续显示 ${formatTime(usableSnapshot.capturedAt)} 的最近成功数据。`
    : "当前没有可替代的成功数据。";
  return <p className="raw-data-error">
    最近尝试：<strong>{status}</strong>（{formatTime(latestAttempt.capturedAt)}）。
    {latestAttempt.error ? `${latestAttempt.error} ` : ""}{fallback}
  </p>;
}

export function CharacterResourcesView({
  snapshot, latestAttempt
}: {
  snapshot?: GameDataSnapshot | null;
  latestAttempt?: GameDataSnapshot | null;
}) {
  const resources = [
    ["金币", snapshot ? formatNumber(snapshot.data.gold) : "—", false],
    ["角色等级", snapshot ? formatNumber(snapshot.data.level) : "—", false],
    ["当前经验", snapshot ? formatNumber(snapshot.data.experience) : "—", false],
    ["升级所需经验", snapshot ? formatNumber(snapshot.data.experienceRequired) : "—", false],
    ["银币", "待接入", true],
    ["仙玉", "待接入", true],
    ["活力", "待接入", true],
    ["评分", "待接入", true]
  ] as const;

  return <section className="raw-data-section" role="tabpanel" id="data-panel-resources" aria-labelledby="data-tab-resources">
    <div className="panel-heading">
      <div><span className="eyebrow">CHARACTER RESOURCES</span><h2>角色资源</h2></div>
      {snapshot && <SnapshotMeta snapshot={snapshot} />}
    </div>
    <LatestAttemptNotice latestAttempt={latestAttempt} usableSnapshot={snapshot} />
    {snapshot ? <>
      {snapshot.error && <p className="raw-data-error">{snapshot.error}</p>}
      <dl className="resource-ledger">{resources.map(([label, value, pending]) => <div key={label} className={pending ? "pending" : ""}>
        <dt>{label}</dt><dd>{value}</dd>
      </div>)}</dl>
    </> : <div className="raw-data-empty">暂无角色资源快照，完成一次数据采集后将在这里显示。</div>}
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
  const pendingTasks = latestAttempt?.status === "partial" ? readDailyTasks(latestAttempt) : [];
  return <section className="raw-data-section" role="tabpanel" id="data-panel-daily" aria-labelledby="data-tab-daily">
    <div className="panel-heading">
      <div><span className="eyebrow">DAILY TASKS</span><h2>今日任务</h2></div>
      <div className="raw-data-actions">
        {snapshot && <SnapshotMeta snapshot={snapshot} />}
        <button className="button" disabled={disabled || capturing} onClick={onCapture}>
          {capturing ? "正在读取…" : "读取当前日程"}
        </button>
      </div>
    </div>
    <LatestAttemptNotice latestAttempt={latestAttempt} usableSnapshot={snapshot} />
    {snapshot?.error && latestAttempt?.id === snapshot.id && <p className="raw-data-error">{snapshot.error}</p>}
    {tasks.length ? <div className="daily-task-table-wrap"><table className="daily-task-table">
      <thead><tr><th>任务</th><th>进度</th><th>状态</th><th>奖励</th></tr></thead>
      <tbody>{tasks.map((task, index) => <tr key={task.taskId || `${task.name}-${index}`}>
        <td>{displayValue(task.name)}</td>
        <td>{taskProgress(task)}</td>
        <td>{TASK_STATUS_LABELS[task.status]}</td>
        <td>{REWARD_STATUS_LABELS[task.rewardStatus]}</td>
      </tr>)}</tbody>
    </table></div> : <div className="raw-data-empty">{snapshot
      ? "本次采集未返回今日任务列表。"
      : "暂无今日任务快照。"}</div>}
    {pendingTasks.length > 0 && <div className="daily-task-table-wrap">
      <div className="raw-data-meta">
        <span>最新尝试待确认，不可用于任务编排</span>
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
