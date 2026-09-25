import { describe, expect, it, vi } from "vitest";

import { createRefreshBreaker, RefreshSuspendedError } from "../refresh-breaker.js";

function clock(start = 1_000_000) {
  let at = start;
  return { now: () => at, advance: (ms: number) => { at += ms; } };
}

describe("createRefreshBreaker", () => {
  it("lets an ordinary session refresh as often as it needs to", () => {
    const time = clock();
    const breaker = createRefreshBreaker({ now: time.now });
    for (let hour = 0; hour < 24; hour += 1) {
      expect(() => breaker.admit()).not.toThrow();
      time.advance(60 * 60_000);
    }
  });

  it("stops a poll that refreshes every 1.5 s after the budget, without calling the server", () => {
    const time = clock();
    const onTrip = vi.fn();
    const breaker = createRefreshBreaker({ now: time.now, onTrip });
    let admitted = 0;
    let refused = 0;
    for (let tick = 0; tick < 40; tick += 1) {
      try {
        breaker.admit();
        admitted += 1;
      } catch (error) {
        expect(error).toBeInstanceOf(RefreshSuspendedError);
        refused += 1;
      }
      time.advance(1_500);
    }
    /* 60 s of polling: four admitted, then a 30 s cooldown, then four more. */
    expect(admitted).toBe(8);
    expect(refused).toBe(32);
    expect(onTrip).toHaveBeenCalledTimes(2);
  });

  it("doubles the cooldown on each trip, up to the ceiling", () => {
    const time = clock();
    const retryAts: number[] = [];
    const breaker = createRefreshBreaker({
      now: time.now,
      budget: 1,
      cooldownMs: 1_000,
      maxCooldownMs: 3_000,
      onTrip: (at) => retryAts.push(at - time.now()),
    });
    for (let trip = 0; trip < 4; trip += 1) {
      breaker.admit();
      expect(() => breaker.admit()).toThrow(RefreshSuspendedError);
      time.advance(retryAts[retryAts.length - 1]);
    }
    expect(retryAts).toEqual([1_000, 2_000, 3_000, 3_000]);
  });

  it("goes back to the short cooldown once it has been quiet for long enough", () => {
    const time = clock();
    const cooldowns: number[] = [];
    const breaker = createRefreshBreaker({
      now: time.now,
      budget: 1,
      cooldownMs: 1_000,
      forgetAfterMs: 10_000,
      onTrip: (at) => cooldowns.push(at - time.now()),
    });
    breaker.admit();
    expect(() => breaker.admit()).toThrow();
    time.advance(1_000);
    breaker.admit();
    expect(() => breaker.admit()).toThrow();
    time.advance(20_000);
    breaker.admit();
    expect(() => breaker.admit()).toThrow();
    expect(cooldowns).toEqual([1_000, 2_000, 1_000]);
  });

  it("says when refreshing may resume", () => {
    const time = clock();
    const breaker = createRefreshBreaker({ now: time.now, budget: 1, cooldownMs: 5_000 });
    expect(breaker.suspendedUntil()).toBeNull();
    breaker.admit();
    expect(() => breaker.admit()).toThrow();
    expect(breaker.suspendedUntil()).toBe(time.now() + 5_000);
    time.advance(5_000);
    expect(breaker.suspendedUntil()).toBeNull();
  });
});
