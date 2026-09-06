import { describe, expect, it } from "vitest";
import {
  formatUsageCost,
  formatUsagePercent,
  usageBreakdown,
} from "./usage-format";

describe("usage presentation", () => {
  it("keeps missing, zero and tiny positive costs distinct", () => {
    expect(formatUsageCost(null)).toBe("Unavailable");
    expect(formatUsageCost(undefined)).toBe("Unavailable");
    expect(formatUsageCost(0)).toBe("$0.00");
    expect(formatUsageCost(0.000000001)).toBe("<$0.0001");
    expect(formatUsageCost(0.000000001, true)).toBe("$0.000000001");
    expect(formatUsageCost(0.00001414, true)).toBe("$0.00001414");
  });
  it("does not round a usable allowance up to exhausted", () => {
    expect(formatUsagePercent(99.99, true)).toBe(">99%");
    expect(formatUsagePercent(0.01, true)).toBe("<1%");
    expect(formatUsagePercent(0, true)).toBe("0%");
    expect(formatUsagePercent(100, false)).toBe("100%");
    expect(formatUsagePercent(120, false)).toBe("120%");
  });
  it("sorts numeric spend and reads total tokens rather than a guessed field", () => {
    expect(
      usageBreakdown({
        a: { system_cost_usd: 9, input_tokens: 4, total_tokens: 7 },
        b: { system_cost_usd: 12, total_tokens: 2 },
      }),
    ).toEqual([
      { label: "b", cost: 12, tokens: 2 },
      { label: "a", cost: 9, tokens: 7 },
    ]);
  });
});
