import { expect, test } from "vitest";
import { trendLineDataOptions } from "./TrendChart";

test("connects adjacent valid metric points across failed captures", () => {
  const options = trendLineDataOptions([
    { capturedAt: "2026-07-26T08:00:00+08:00", value: 100, status: "success" },
    { capturedAt: "2026-07-26T09:00:00+08:00", value: null, status: "failed" },
    { capturedAt: "2026-07-27T08:00:00+08:00", value: 160, status: "success" },
  ]);

  expect(options.connectNulls).toBe(true);
  expect(options.data).toEqual([100, null, 160]);
});
