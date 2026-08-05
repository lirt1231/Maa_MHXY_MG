import { expect, test } from "vitest";
import { successfulTrendPoints, trendLineDataOptions, trendWindowBounds } from "./TrendChart";

test("omits failed captures from both trend values and horizontal-axis points", () => {
  const points = [
    { capturedAt: "2026-07-26T08:00:00+08:00", value: 100, status: "success" },
    { capturedAt: "2026-07-31T08:09:23+08:00", value: null, status: "failed" },
    { capturedAt: "2026-07-27T08:00:00+08:00", value: 160, status: "success" },
  ];
  const visiblePoints = successfulTrendPoints(points);
  const options = trendLineDataOptions(points);

  expect(options.connectNulls).toBe(true);
  expect(options.data).toEqual([
    ["2026-07-26T08:00:00+08:00", 100],
    ["2026-07-27T08:00:00+08:00", 160],
  ]);
  expect(visiblePoints.map((item) => item.capturedAt)).toEqual([
    "2026-07-26T08:00:00+08:00",
    "2026-07-27T08:00:00+08:00",
  ]);
});

test("uses the selected range as the real horizontal time window", () => {
  const now = Date.parse("2026-08-04T00:00:00Z");
  const seven = trendWindowBounds(7, now);
  const thirty = trendWindowBounds(30, now);

  expect(seven.max).toBe(now);
  expect(thirty.max).toBe(now);
  expect(seven.min - thirty.min).toBe(23 * 24 * 60 * 60 * 1000);
});
