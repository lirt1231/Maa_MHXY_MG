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
  risk: { level: "low" | "medium" | "high"; label: string; summary: string };
  steps: string[];
  checklist: string[];
  warnings: string[];
  parameters: TaskParameter[];
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
