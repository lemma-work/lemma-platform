import { describe, expect, it, vi } from "vitest";

import {
  addAltchaProof,
  fetchAltchaEnabled,
  getAltchaProgress,
} from "./altcha";

describe("ALTCHA client state", () => {
  it("reads whether proof-of-work protection is enabled", async () => {
    const enabledFetch = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ enabled: true }), { status: 200 }),
    );
    await expect(fetchAltchaEnabled(enabledFetch)).resolves.toBe(true);

    const unavailableFetch = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new Error("offline"));
    await expect(fetchAltchaEnabled(unavailableFetch)).resolves.toBeNull();
  });

  it("does not alter requests when protection is disabled", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ enabled: false }), { status: 200 }),
    );
    const request = { headers: { Accept: "application/json" } };

    await expect(addAltchaProof(request, "signup", fetcher)).resolves.toBe(
      request,
    );
    expect(getAltchaProgress()).toEqual({ phase: "idle", enabled: false });
  });

  it("fails closed and reports an unsolved challenge", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          enabled: true,
          algorithm: "SHA-256",
          challenge: "not-a-valid-digest",
          maxnumber: 0,
          salt: "test-salt",
          signature: "test-signature",
        }),
        { status: 200 },
      ),
    );

    await expect(addAltchaProof({}, "signup", fetcher)).rejects.toThrow(
      "Unable to solve the proof-of-work challenge",
    );
    expect(getAltchaProgress()).toEqual({ phase: "error", enabled: true });
  });
});
