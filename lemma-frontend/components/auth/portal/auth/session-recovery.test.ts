import { describe, expect, it, vi } from "vitest";

import {
  clearFrontendSessionState,
  cookieDomainsFor,
  isUnrepairableSessionFailure,
  type SessionStateStore,
} from "@/components/auth/portal/auth/session-recovery";

/** The message the SuperTokens fetch interceptor throws at its retry ceiling. */
const CEILING_MESSAGE =
  "Received a 401 response from http://app.lemma.localhost:64820/users/me. " +
  "Attempted to refresh the session and retry the request with the updated " +
  "session tokens 2 times, but each attempt resulted in a 401 error. The " +
  "maximum session refresh limit has been reached. Please investigate your " +
  "API. To increase the session refresh attempts, update " +
  "maxRetryAttemptsForSessionRefresh in the config.";

describe("isUnrepairableSessionFailure", () => {
  it("recognises the refresh ceiling, which is the state refreshing cannot fix", () => {
    expect(isUnrepairableSessionFailure(new Error(CEILING_MESSAGE))).toBe(true);
  });

  it("leaves ordinary failures alone so a blip does not sign anyone out", () => {
    expect(isUnrepairableSessionFailure(new Error("Failed to fetch"))).toBe(
      false,
    );
    expect(isUnrepairableSessionFailure(new Error("Unable to load user: 500"))).toBe(
      false,
    );
    expect(isUnrepairableSessionFailure(undefined)).toBe(false);
  });
});

describe("cookieDomainsFor", () => {
  it("covers every parent the session cookie could have been widened to", () => {
    // Stops before the bare last label: the SDK would never have set a
    // cookie there, so asking the browser to delete one is noise.
    expect(cookieDomainsFor("app.lemma.localhost")).toEqual([
      ".app.lemma.localhost",
      ".lemma.localhost",
    ]);
  });

  it("offers nothing to delete on a single-label host", () => {
    expect(cookieDomainsFor("localhost")).toEqual([]);
  });
});

describe("clearFrontendSessionState", () => {
  it("clears the front token the app reads, host-only and on every parent", () => {
    const expireCookie = vi.fn();
    const removeStored = vi.fn();
    const store: SessionStateStore = {
      cookieDomains: [".lemma.localhost"],
      expireCookie,
      removeStored,
    };

    const cleared = clearFrontendSessionState(store);

    expect(cleared).toContain("sFrontToken");
    expect(removeStored).toHaveBeenCalledWith("sFrontToken");
    expect(expireCookie).toHaveBeenCalledWith("sFrontToken", null);
    expect(expireCookie).toHaveBeenCalledWith("sFrontToken", ".lemma.localhost");
  });

  it("clears the header-mode tokens too, so neither transfer method survives", () => {
    const removeStored = vi.fn();
    clearFrontendSessionState({
      cookieDomains: [],
      expireCookie: vi.fn(),
      removeStored,
    });

    const cleared = removeStored.mock.calls.map(([name]) => name);
    expect(cleared).toEqual(
      expect.arrayContaining(["st-access-token", "st-refresh-token"]),
    );
  });
});
