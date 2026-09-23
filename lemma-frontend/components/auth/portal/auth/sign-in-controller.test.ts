import { describe, expect, it, vi } from "vitest";

import {
  appendSignUpMarker,
  applyContinueResult,
  beginHandoff,
  beginResolve,
  changeEmail,
  continueRequest,
  initialSignInState,
  passwordFallbackFor,
  resolveAuthMode,
  runPasswordSignIn,
  shouldRenderIdentifierFirst,
  signInErrorMessage,
  validateIdentifier,
  type SignInState,
} from "./sign-in-controller";

const PROVIDERS = [
  { id: "google", name: "Google" },
  { id: "active-directory", name: "Microsoft" },
] as const;

const CHALLENGE = {
  method: "code",
  challenge_id: "challenge-1",
  expires_at: "2026-09-10T10:10:00Z",
} as const;

function resolving(overrides: Partial<SignInState> = {}): SignInState {
  return { ...beginResolve(initialSignInState, "Ada@Example.com "), ...overrides };
}

describe("identifier step", () => {
  it("requires something to look up, and normalizes what it gets", () => {
    expect(validateIdentifier("   ")).toBe("Enter your email address.");
    expect(validateIdentifier("ada@example.com")).toBeNull();
    expect(resolving().email).toBe("ada@example.com");
  });

  it("keeps the nonce and clears the previous error when starting again", () => {
    const state = resolving({ nonce: "browser-nonce", error: "boom" });
    const next = beginResolve(state, "ada@example.com");
    expect(next.nonce).toBe("browser-nonce");
    expect(next.error).toBeNull();
    expect(next.step).toBe("resolving");
  });
});

describe("applying what /continue answered", () => {
  it("routes a password account to the password field", () => {
    const next = applyContinueResult(
      resolving({ abandonChallengeId: "old" }),
      "n",
      { method: "password" },
      PROVIDERS,
    );
    expect(next.step).toBe("password");
    expect(next.abandonChallengeId).toBeNull();
    expect(next.email).toBe("ada@example.com");
  });

  it("routes a third-party account to its provider", () => {
    const next = applyContinueResult(
      resolving(),
      "n",
      { method: "thirdparty", provider: "google" },
      PROVIDERS,
    );
    expect(next.step).toBe("handoff");
    expect(next.provider).toEqual({ id: "google", name: "Google" });
  });

  it("says what does work when the provider this account uses is unavailable", () => {
    // It must not offer a code. Nothing on the identifier step renders that
    // offer, and a code could not be honoured for a third-party account anyway.
    const next = applyContinueResult(
      resolving(),
      "n",
      { method: "thirdparty", provider: "active-directory" },
      [],
    );
    expect(next.step).toBe("identifier");
    expect(next.error).toContain("Microsoft");
    expect(next.error).toContain("browser");
    expect(next.error).not.toContain("code");
    expect(next.passwordFallback).toBe(false);
  });

  it("hands off to a provider the person picked themselves", () => {
    const next = beginHandoff(resolving({ error: "stale" }), PROVIDERS[0]);
    expect(next.step).toBe("handoff");
    expect(next.provider).toEqual({ id: "google", name: "Google" });
    expect(next.error).toBeNull();
  });

  it("carries the challenge through to the code step", () => {
    const next = applyContinueResult(resolving(), "n", CHALLENGE, PROVIDERS);
    expect(next.step).toBe("code");
    expect(next.challenge).toEqual({
      challenge_id: "challenge-1",
      expires_at: "2026-09-10T10:10:00Z",
    });
  });
});

describe("abandoning a challenge", () => {
  it("names the live challenge when leaving the code step", () => {
    const code = applyContinueResult(resolving(), "n", CHALLENGE, PROVIDERS);
    expect(changeEmail(code).abandonChallengeId).toBe("challenge-1");
  });

  it("has nothing to abandon when leaving the password step", () => {
    const password = applyContinueResult(
      resolving(),
      "n",
      { method: "password" },
      PROVIDERS,
    );
    expect(changeEmail(password).abandonChallengeId).toBeNull();
  });

  it("keeps owing the cancellation until a continue actually succeeds", () => {
    // The cooldown this clears is held against the binding, so a second detour
    // that had already dropped the id would arrive with nothing to retire.
    const code = applyContinueResult(resolving(), "n", CHALLENGE, PROVIDERS);
    const back = changeEmail(code);
    expect(continueRequest(back, "other@example.com", "n").abandonChallengeId).toBe(
      "challenge-1",
    );
    const failedAgain = beginResolve(back, "third@example.com");
    expect(continueRequest(failedAgain, "third@example.com", "n").abandonChallengeId).toBe(
      "challenge-1",
    );
    const settled = applyContinueResult(back, "n", { method: "password" }, PROVIDERS);
    expect(continueRequest(settled, "x@example.com", "n").abandonChallengeId).toBeNull();
  });
});

describe("password sign-in", () => {
  it("reports an authenticated result", async () => {
    expect(await runPasswordSignIn(vi.fn().mockResolvedValue({ status: "OK" }))).toEqual({
      outcome: "authenticated",
    });
  });

  it("does not echo the address back in a wrong-password message", async () => {
    const outcome = await runPasswordSignIn(
      vi.fn().mockResolvedValue({ status: "WRONG_CREDENTIALS_ERROR" }),
    );
    expect(outcome).toMatchObject({ outcome: "rejected", fallback: false });
    expect(outcome).not.toHaveProperty("message", expect.stringContaining("@"));
  });

  it("offers the code fallback for a passwordless refusal, and not otherwise", async () => {
    const passwordless = await runPasswordSignIn(
      vi.fn().mockResolvedValue({
        status: "SIGN_IN_NOT_ALLOWED",
        reason: "This email uses email-code login. Choose Continue with email code.",
      }),
    );
    expect(passwordless).toEqual({
      outcome: "rejected",
      message: "This email uses email-code login. Choose Continue with email code.",
      fallback: true,
    });

    const suspended = await runPasswordSignIn(
      vi.fn().mockResolvedValue({
        status: "SIGN_IN_NOT_ALLOWED",
        reason: "Unable to sign in with these credentials",
      }),
    );
    expect(suspended).toMatchObject({ fallback: false });
  });

  it("surfaces a field error and a thrown message verbatim", async () => {
    expect(
      await runPasswordSignIn(
        vi.fn().mockResolvedValue({
          status: "FIELD_ERROR",
          formFields: [{ id: "password", error: "Password is required" }],
        }),
      ),
    ).toMatchObject({ message: "Password is required" });

    // `runAuthRequest` has already rendered a rate limit into prose on the
    // error; reading the status is not an option because it never arrives.
    expect(
      await runPasswordSignIn(
        vi.fn().mockRejectedValue(new Error("Too many sign-in attempts.")),
      ),
    ).toMatchObject({ message: "Too many sign-in attempts." });
  });

  it("falls back to offline copy for a throw that carries nothing", () => {
    expect(signInErrorMessage({})).toContain("couldn’t reach Lemma");
  });

  it("matches the passwordless reason loosely", () => {
    expect(passwordFallbackFor("This email uses EMAIL-CODE LOGIN.")).toBe(true);
    expect(passwordFallbackFor("Please sign in using your password.")).toBe(false);
  });
});

describe("which screen owns this render", () => {
  it("sees the prebuilt sign-up marker even beside an existing query", () => {
    // Pins the SuperTokens convention this whole branch depends on.
    expect(resolveAuthMode("/auth", "?redirect_uri=x&show=signup", "")).toBe("signup");
    expect(resolveAuthMode("/auth", "?redirect_uri=x", "")).toBe("signin");
  });

  it("never takes the third-party callback, which reads as signin", () => {
    // `/callback/google` matches none of signup|signin|login, so `resolveAuthMode`
    // falls through to its default. Mounting here would swallow the OAuth code.
    expect(resolveAuthMode("/callback/google", "", "")).toBe("signin");
    expect(
      shouldRenderIdentifierFirst({
        authMode: "signin",
        isThirdPartyCallbackRoute: true,
        routeIsHandledByPreBuiltUi: true,
      }),
    ).toBe(false);
  });

  it("leaves signup and unrecognised routes to the prebuilt UI", () => {
    expect(
      shouldRenderIdentifierFirst({
        authMode: "signup",
        isThirdPartyCallbackRoute: false,
        routeIsHandledByPreBuiltUi: true,
      }),
    ).toBe(false);
    expect(
      shouldRenderIdentifierFirst({
        authMode: "signin",
        isThirdPartyCallbackRoute: false,
        routeIsHandledByPreBuiltUi: false,
      }),
    ).toBe(false);
    expect(
      shouldRenderIdentifierFirst({
        authMode: "signin",
        isThirdPartyCallbackRoute: false,
        routeIsHandledByPreBuiltUi: true,
      }),
    ).toBe(true);
  });

  it("hands the URL back to the prebuilt UI without losing the destination", () => {
    const handed = appendSignUpMarker("?redirect_uri=%2Fpods&desktop_request=abc");
    const params = new URLSearchParams(handed);
    expect(params.get("show")).toBe("signup");
    expect(params.get("redirect_uri")).toBe("/pods");
    expect(params.get("desktop_request")).toBe("abc");
    expect(appendSignUpMarker(handed)).toBe(handed);
  });
});
