import { describe, expect, it, vi } from "vitest";

import {
  runVerificationAttempt,
  runVerificationSend,
  refreshVerifiedSession,
  signOutForDifferentAccount,
  verificationCooldownSeconds,
  verificationDestination,
  verificationTokenFromSearch,
} from "./verification-controller";

describe("verification controller", () => {
  it("maps successful, invalid, and failed token verification", async () => {
    expect(
      await runVerificationAttempt(vi.fn().mockResolvedValue({ status: "OK" })),
    ).toBe("verified");
    expect(
      await runVerificationAttempt(
        vi.fn().mockResolvedValue({
          status: "EMAIL_VERIFICATION_INVALID_TOKEN_ERROR",
        }),
      ),
    ).toBe("invalid");
    expect(
      await runVerificationAttempt(vi.fn().mockRejectedValue(new Error("offline"))),
    ).toBe("error");
  });

  it("maps sent, already-verified, and failed resend requests", async () => {
    expect(
      await runVerificationSend(vi.fn().mockResolvedValue({ status: "OK" })),
    ).toBe("sent");
    expect(
      await runVerificationSend(
        vi.fn().mockResolvedValue({ status: "EMAIL_ALREADY_VERIFIED_ERROR" }),
      ),
    ).toBe("already-verified");
    expect(
      await runVerificationSend(vi.fn().mockRejectedValue(new Error("offline"))),
    ).toBe("error");
  });

  it("reads tokens without losing tenant or redirect query parameters", () => {
    expect(
      verificationTokenFromSearch(
        "?tenantId=public&token=abc123&redirect_uri=https%3A%2F%2Fexample.com",
      ),
    ).toBe("abc123");
    expect(verificationTokenFromSearch("?tenantId=public")).toBeNull();
  });

  it("counts down resend availability and clamps at zero", () => {
    expect(verificationCooldownSeconds(1_000, 1_000)).toBe(30);
    expect(verificationCooldownSeconds(1_000, 30_001)).toBe(1);
    expect(verificationCooldownSeconds(1_000, 31_000)).toBe(0);
    expect(verificationCooldownSeconds(null, 1_000)).toBe(0);
  });

  it("preserves redirect destinations and prioritises desktop handoff", () => {
    expect(
      verificationDestination({
        desktopRequestId: null,
        authLandingUrl: "https://lemma.work/auth",
        redirectUri: "https://app.example.test/after-auth",
        defaultRedirect: "https://lemma.work/",
      }),
    ).toBe("https://app.example.test/after-auth");
    expect(
      verificationDestination({
        desktopRequestId: "request-123",
        authLandingUrl: "https://lemma.work/auth",
        redirectUri: "https://app.example.test/after-auth",
        defaultRedirect: "https://lemma.work/",
      }),
    ).toBe(
      "https://lemma.work/auth?desktop_browser=1&desktop_request=request-123",
    );
  });

  it("refreshes the verified session and clears redirects after sign-out", async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    const signOut = vi.fn().mockResolvedValue(undefined);
    const clearRedirect = vi.fn();

    await expect(refreshVerifiedSession(refresh)).resolves.toBe(true);
    await signOutForDifferentAccount(signOut, clearRedirect);

    expect(refresh).toHaveBeenCalledOnce();
    expect(signOut).toHaveBeenCalledOnce();
    expect(clearRedirect).toHaveBeenCalledOnce();
  });
});
