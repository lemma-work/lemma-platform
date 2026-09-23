import { describe, expect, it } from "vitest";
import {
  formatUsageShare,
  formatUsagePercent,
  usageBreakdown,
} from "./usage-format";

describe("usage presentation", () => {
  it("states a row's share of the period, never an amount of money", () => {
    // Usage is not quantified in money anywhere a customer reads it. This
    // asserted currency strings until the page stopped rendering any.
    expect(formatUsageShare(2.5, 10)).toBe("25%");
    expect(formatUsageShare(0.4, 10)).toBe("4.0%");
    expect(formatUsageShare(10, 10)).toBe("100%");
  });

  it("keeps missing, unknowable and vanishingly small shares distinct", () => {
    expect(formatUsageShare(null, 10)).toBe("Unavailable");
    expect(formatUsageShare(undefined, 10)).toBe("Unavailable");
    // Nothing recorded yet, so a share of it is not a number worth printing.
    expect(formatUsageShare(1, 0)).toBe("—");
    expect(formatUsageShare(1, null)).toBe("—");
    // A share that rounds to zero is not the same as no usage at all.
    expect(formatUsageShare(0.00001, 10)).toBe("<0.1%");
  });

  it("never returns a currency symbol", () => {
    const outputs = [
      formatUsageShare(2.5, 10),
      formatUsageShare(0, 10),
      formatUsageShare(0.00001, 10),
      formatUsageShare(null, 10),
      formatUsageShare(1, 0),
    ];

    for (const output of outputs) {
      expect(output).not.toContain("$");
    }
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
