export type TaskParameter = {
  key: string;
  label: string;
  type: "integer" | "select" | "boolean";
  default: number | string | boolean;
  minimum?: number;
  maximum?: number;
  choices?: { value: string; label: string }[];
};

export type Task = {
  id: string;
  name: string;
  eyebrow: string;
  summary: string;
  description: string;
  status: string;
  category?: {
    id: "daily" | "weekly" | "repeatable";
    label: string;
    order: number;
  };
  taskOrder?: number;
  schedule?: {
    weekdays: number[];
    startTime?: string;
    label: string;
    unavailableReason: string;
  };
  risk: { level: "low" | "medium" | "high"; label: string; summary: string };
  steps: string[];
  checklist: string[];
  warnings: string[];
  parameters: TaskParameter[];
  guideImage?: { src: string; alt: string; caption: string };
  availability?: {
    available: boolean;
    label: string | null;
    reason: string | null;
  };
  todayStatus: {
    status: "completed" | "pending";
    completedAt: string | null;
    resultStatus: "completed" | "already_completed" | null;
    source?: "manual";
  };
};

export type TaskInspection = {
  taskId: string;
  status: "ready" | "unsupported";
  canStart: boolean;
  scene: string;
  sceneLabel: string;
  summary: string;
  proposedAction?: string | null;
  reason?: string | null;
  evidence: {
    imageSize?: [number, number] | null;
    ocrItemCount?: number;
    matchedTemplates?: string[];
    frameId?: string;
    captureMethod?: string;
    windowBoundsPoints?: [number, number, number, number];
    contentRectPoints?: [number, number, number, number];
    scaleFactor?: number;
    anchorValidation?: string;
    recognizedBoxCenterPixels?: [number, number];
    predictedScreenPoint?: [number, number];
  };
  observedAt: string;
};

export type Run = {
  id: string;
  taskId: string;
  taskName: string;
  status: string;
  stage?: string;
  options: Record<string, unknown>;
  startedAt: string;
  finishedAt?: string | null;
  durationMs?: number | null;
  error?: string | null;
  result?: TaskRunResult | null;
  benefitStatus?: string;
  resourceYield?: TaskResourceYield | null;
  failurePhase?: "task" | "recovery" | null;
  homeRecovery?: {
    status: "not_requested" | "running" | "confirmed" | "failed" | "cancelled";
    outcome?: "already_main" | "recovered" | null;
    scene?: string | null;
    reason?: string | null;
    actions?: number;
    clicks?: number;
    confirmedAt?: string | null;
    evidence?: {
      targetId: string;
      firstFrameId: string;
      lastFrameId: string;
      anchors: string[];
      imageSize?: [number, number];
      captureMethod?: string;
    };
  };
};

export type ResourceSnapshot = {
  id: number;
  targetId: string;
  characterId?: string | null;
  phase: "before" | "after";
  capturedAt: string;
  status: "success" | "partial" | "failed";
  values: {
    level: number | null;
    experience: number | null;
    experienceRequired: number | null;
    gold: number | null;
    silver: number | null;
  };
  evidence: Record<string, unknown>;
  error?: string | null;
  capturedFields?: ("level" | "experience" | "experience_required" | "gold" | "silver")[];
};

export type TaskResourceYield = {
  taskStatus: string;
  attributionStatus: "reliable" | "interrupted" | "cross_level_unknown" | "partial" | "unavailable" | "not_run";
  beforeSnapshotId: number;
  afterSnapshotId: number | null;
  experienceDelta: number | null;
  goldDelta: number | null;
  silverDelta: number | null;
  reasons: string[];
  trackedFields?: TaskYieldMetric[];
  calculationVersion: number;
  createdAt: string;
  before: ResourceSnapshot;
  after: ResourceSnapshot | null;
};

export type TaskYieldMetric = "experience" | "gold" | "silver";

export type TaskYieldMetricStats = {
  average: number | null;
  total: number | null;
  sampleCount: number;
  minimum: number | null;
  maximum: number | null;
};

export type TaskYieldPoint = {
  runId: string;
  completedAt: string;
  attributionStatus: TaskResourceYield["attributionStatus"];
  experienceDelta: number | null;
  goldDelta: number | null;
  silverDelta: number | null;
  validMetrics: TaskYieldMetric[];
};

export type TaskYieldTaskStatistics = {
  taskId: string;
  taskName: string;
  attempts: number;
  completedRuns: number;
  reliableRuns: number;
  coverageRate: number;
  latestAt: string | null;
  metrics: Record<TaskYieldMetric, TaskYieldMetricStats>;
  points: TaskYieldPoint[];
};

export type TaskYieldStatistics = {
  rangeDays: number;
  tasks: TaskYieldTaskStatistics[];
};

type TaskRunProgress = {
  task_id: string;
  questions?: number;
  answers?: number;
  clicks?: number;
  limit?: number;
  rounds?: number;
  target_rounds?: number;
  actions?: number;
  steps?: number;
  ghost_progress?: number;
  max_steps?: number;
};

export type TaskRunResult = TaskRunProgress & (
  | { status: "safe_stopped"; reason: string }
  | { status: "completed" | "already_completed" | "limit_reached" | "cancelled"; reason?: string }
);

export type SystemState = {
  platform: string;
  gameRunning: boolean;
  windowFound: boolean;
  windowName?: string | null;
  screenCapturePermission: boolean;
  accessibilityPermission: boolean;
  available: boolean;
  status: string;
  error?: string | null;
  run?: Run | null;
  metricCollectedToday: boolean;
};

export type Metrics = {
  metric: "gold" | "experience";
  rangeDays: number;
  current: number | null;
  change: number | null;
  stats: MetricStats;
  points: { capturedAt: string; value: number | null; status: string }[];
  snapshots: MetricSnapshot[];
};

export type MetricStats = {
  attempts: number;
  successfulCaptures: number;
  failedCaptures: number;
  correctedCaptures: number;
  captureSuccessRate: number;
  validValues: number;
  minimum: number | null;
  maximum: number | null;
  average: number | null;
  latestChange: number | null;
  averageChangePerCapture: number | null;
  daysWithData: number;
};

export type MetricSnapshot = {
  id: number;
  capturedAt: string;
  gold: number | null;
  level: number | null;
  experience: number | null;
  experienceRequired: number | null;
  status: string;
  error?: string | null;
  corrected: boolean;
};

export type GameDataSnapshot = {
  id: number;
  capturedAt: string;
  gameDate: string;
  schemaVersion: number;
  targetId: string;
  category: string;
  sourcePage: string;
  status: "success" | "partial" | "failed";
  stale: boolean;
  data: Record<string, unknown>;
  evidence: Record<string, unknown>;
  error?: string | null;
};

export type GameDataResponse = {
  targetId: string;
  items: GameDataSnapshot[];
  latestByCategory: Record<string, GameDataSnapshot | null>;
  latestAttemptByCategory: Record<string, GameDataSnapshot | null>;
  latestUsableByCategory: Record<string, GameDataSnapshot | null>;
};

export type DailyTaskItem = {
  taskId?: string | null;
  name: string;
  completed: number | null;
  target: number | null;
  status: "completed" | "available" | "locked" | "unknown";
  rewardStatus: "claimable" | "claimed" | "unknown";
};

export type RunSummary = {
  total: number;
  succeeded: number;
  failed: number;
  successRate: number;
  averageDurationMs: number;
};

export type LiveEvent = {
  id?: number;
  runId: string;
  createdAt: string;
  level: string;
  type: string;
  message: string;
  payload: Record<string, unknown>;
};
