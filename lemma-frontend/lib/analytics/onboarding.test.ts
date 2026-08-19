import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { elapsedBucket } from "./onboarding";

describe("elapsedBucket", () => {
    const now = new Date("2026-08-18T12:00:00.000Z");

    beforeEach(() => {
        vi.useFakeTimers();
        vi.setSystemTime(now);
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    const at = (minutesAgo: number) =>
        new Date(now.getTime() - minutesAgo * 60_000).toISOString();

    it("bands elapsed time since signup", () => {
        expect(elapsedBucket(at(0.5))).toBe("under_1m");
        expect(elapsedBucket(at(3))).toBe("1_5m");
        expect(elapsedBucket(at(10))).toBe("5_15m");
        expect(elapsedBucket(at(40))).toBe("15_60m");
        expect(elapsedBucket(at(120))).toBe("over_1h");
    });

    it("puts the boundaries in the later band", () => {
        expect(elapsedBucket(at(1))).toBe("1_5m");
        expect(elapsedBucket(at(5))).toBe("5_15m");
        expect(elapsedBucket(at(15))).toBe("15_60m");
        expect(elapsedBucket(at(60))).toBe("over_1h");
    });

    it("reports unknown rather than guessing", () => {
        expect(elapsedBucket(null)).toBe("unknown");
        expect(elapsedBucket(undefined)).toBe("unknown");
        expect(elapsedBucket("not a date")).toBe("unknown");
    });

    it("refuses a signup in the future, which would flatter the funnel", () => {
        expect(elapsedBucket(at(-30))).toBe("unknown");
    });
});
