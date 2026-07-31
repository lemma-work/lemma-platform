import STGeneralError from "supertokens-web-js/utils/error";
import { describe, expect, it } from "vitest";

import {
  authErrorMessageFromResponse,
  formatRetryDelay,
  runAuthRequest,
} from "./auth-errors";

describe("auth error presentation", () => {
  it("turns a 429 into a specific retry message", async () => {
    const response = new Response(
      JSON.stringify({
        status: "GENERAL_ERROR",
        message: "Too many authentication attempts",
      }),
      { status: 429, headers: { "Retry-After": "125" } },
    );

    await expect(authErrorMessageFromResponse(response, "sign-in")).resolves.toBe(
      "Too many sign-in attempts. Please try again in 3 minutes.",
    );
  });

  it("uses request-specific rate-limit wording", async () => {
    const response = new Response("{}", {
      status: 429,
      headers: { "Retry-After": "42" },
    });

    await expect(
      authErrorMessageFromResponse(response, "password-reset"),
    ).resolves.toBe(
      "Too many password reset requests. Please try again in 42 seconds.",
    );
  });

  it("humanizes retry durations", () => {
    expect(formatRetryDelay(1)).toBe("1 second");
    expect(formatRetryDelay(59)).toBe("59 seconds");
    expect(formatRetryDelay(60)).toBe("1 minute");
    expect(formatRetryDelay(61)).toBe("2 minutes");
  });

  it("maps proof and network failures without exposing internals", async () => {
    const proofFailure = new Response(
      JSON.stringify({
        status: "GENERAL_ERROR",
        message: "Proof-of-work expired or already used",
      }),
      { status: 400 },
    );

    await expect(
      authErrorMessageFromResponse(proofFailure, "sign-up"),
    ).resolves.toContain("private security check expired");

    await expect(
      runAuthRequest("sign-in", async () => {
        throw new TypeError("fetch failed");
      }),
    ).rejects.toMatchObject({
      message: "We couldn’t reach Lemma. Check your connection and try again.",
    });
  });

  it("preserves SuperTokens general errors", async () => {
    const original = new STGeneralError("Invalid credentials");
    await expect(
      runAuthRequest("sign-in", async () => {
        throw original;
      }),
    ).rejects.toBe(original);
  });
});
