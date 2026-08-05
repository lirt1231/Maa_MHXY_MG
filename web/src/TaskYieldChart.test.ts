import { expect, test } from "vitest";
import { validTaskYieldPoints } from "./TaskYieldChart";
import type { TaskYieldPoint } from "./types";

const points: TaskYieldPoint[] = [
  {
    runId: "reliable", completedAt: "2026-08-01T10:00:00Z",
    attributionStatus: "reliable", experienceDelta: 100, goldDelta: 20,
    silverDelta: 300, validMetrics: ["experience", "gold", "silver"],
  },
  {
    runId: "level-up", completedAt: "2026-08-02T10:00:00Z",
    attributionStatus: "cross_level_unknown", experienceDelta: null, goldDelta: 30,
    silverDelta: 400, validMetrics: ["gold", "silver"],
  },
];

test("uses only points that are valid for the selected resource", () => {
  expect(validTaskYieldPoints(points, "experience")).toEqual([
    { runId: "reliable", completedAt: "2026-08-01T10:00:00Z", value: 100 },
  ]);
  expect(validTaskYieldPoints(points, "gold").map((point) => point.value)).toEqual([20, 30]);
});
