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
  steps: string[];
  checklist: string[];
  warnings: string[];
  parameters: TaskParameter[];
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
  benefitStatus?: string;
};

export type SystemState = {
  platform: string;
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
  current: number | null;
  change: number | null;
  points: { capturedAt: string; value: number | null; status: string }[];
  snapshots: MetricSnapshot[];
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
