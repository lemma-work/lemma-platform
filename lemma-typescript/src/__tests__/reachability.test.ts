import { describe, expect, it, vi } from "vitest";

import { probeReachable, reconnectDelay, refreshFailureKind } from "../reachability.js";

describe("refreshFailureKind", () => {
  it("reads a gateway or server failure as unreachable, and a refusal as absent", () => {
    expect(refreshFailureKind(new Response(null, { status: 502 }))).toBe("unreachable");
    expect(refreshFailureKind(new Response(null, { status: 429 }))).toBe("unreachable");
    expect(refreshFailureKind(new Response(null, { status: 401 }))).toBe("absent");
    expect(refreshFailureKind({ statusCode: 503, name: "ServerError" })).toBe("unreachable");
    expect(refreshFailureKind({ statusCode: 403, name: "ForbiddenError" })).toBe("absent");
  });

  it("reads a request that never got an answer as unreachable", () => {
    expect(refreshFailureKind(new TypeError("Failed to fetch"))).toBe("unreachable");
    expect(refreshFailureKind({ name: "NetworkError" })).toBe("unreachable");
  });

  it("reads anything else, including the SDK's own errors, as absent", () => {
    expect(refreshFailureKind(new Error("The 'front-token' header is missing"))).toBe("absent");
    expect(refreshFailureKind(undefined)).toBe("absent");
  });
});

describe("reconnectDelay", () => {
  it("doubles from a second and stops at thirty", () => {
    expect([0, 1, 2, 3, 4, 5, 6, 50].map(reconnectDelay)).toEqual([
      1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000, 30_000,
    ]);
    expect(reconnectDelay(-3)).toBe(1_000);
  });
});

describe("probeReachable", () => {
  it("asks the liveness probe without credentials", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response("{}", { status: 200 }));
    expect(await probeReachable("https://api.x.test/", fetchImpl)).toBe(true);
    expect(fetchImpl.mock.calls[0][0]).toBe("https://api.x.test/health/live");
    expect(fetchImpl.mock.calls[0][1]?.credentials).toBe("omit");
  });

  it("counts any answer but a server failure as up, a 404 included", async () => {
    const answer = (status: number) => vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status }));
    expect(await probeReachable("/_lemma", answer(404))).toBe(true);
    expect(await probeReachable("/_lemma", answer(503))).toBe(false);
    expect(await probeReachable("/_lemma", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("down")))).toBe(false);
  });
});
