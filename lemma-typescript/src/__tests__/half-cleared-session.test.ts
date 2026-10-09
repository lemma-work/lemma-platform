import Session from "supertokens-web-js/recipe/session/index.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthManager, resetMarkerRecoveryForTests } from "../auth.js";
import { ensureCookieSessionSupport } from "../supertokens.js";

/* The real SuperTokens browser SDK, with only the network faked. `auth.test.ts`
   mocks the session recipe away, so it cannot notice the library changing the
   behaviour this recovery depends on: the update marker answering "no session"
   without asking, the event it fires while doing so, and dropping the marker
   bringing the question back. */

const fetchMock = vi.fn<typeof fetch>();
// SuperTokens captures `fetch` when it is initialised, which is lazily, on the
// first auth check -- so this is the fetch underneath its interceptor.
globalThis.fetch = fetchMock;

const API = "https://api.x.test";
const REFRESH = `${API}/st/auth/session/refresh`;
const MARKER = "st-last-access-token-update";

function frontToken(): string {
  return btoa(JSON.stringify({ uid: "u1", ate: Date.now() + 3_600_000, up: {} }));
}

function server(refreshStatus: number): void {
  fetchMock.mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url === REFRESH) {
      if (refreshStatus === 200) {
        return new Response("{}", { status: 200, headers: { "front-token": frontToken() } });
      }
      // Only the API refuses a session; a gateway error says nothing about it.
      const headers: Record<string, string> = refreshStatus === 401 ? { "front-token": "remove" } : {};
      return new Response("{}", { status: refreshStatus, headers });
    }
    if (url === `${API}/users/me`) {
      return new Response(JSON.stringify({ id: "u1", email: "a@x.test" }), { status: 200 });
    }
    return new Response(null, { status: 404 });
  });
}

function calledUrls(): string[] {
  return fetchMock.mock.calls.map(([input]) =>
    typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
  );
}

function clearCookies(): void {
  for (const name of [MARKER, "sFrontToken"]) {
    document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  }
}

describe("a pod app whose host kept the update marker from an ended session", () => {
  beforeEach(() => {
    clearCookies();
    resetMarkerRecoveryForTests();
    fetchMock.mockReset();
    // A failed refresh on this host left the marker and took the front token.
    document.cookie = `${MARKER}=1700000000000; path=/`;
    // Back from the sign-in portal, the way the loop arrives.
    vi.spyOn(document, "referrer", "get").mockReturnValue("https://auth.x.test/");
  });

  afterEach(() => {
    clearCookies();
    vi.restoreAllMocks();
  });

  it("is told there is no session without the server being asked", async () => {
    server(200);
    ensureCookieSessionSupport(API);

    expect(await Session.doesSessionExist()).toBe(false);
    expect(await Session.attemptRefreshingSession()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refreshes once against a separate API host and is signed in", async () => {
    server(200);
    const auth = new AuthManager(API, "https://auth.x.test");

    expect((await auth.checkAuth()).status).toBe("authenticated");
    expect(calledUrls()).toEqual([REFRESH, `${API}/users/me`]);
    expect(document.cookie).toContain("sFrontToken=");
  });

  it("asks once when the session really is over, and is signed out", async () => {
    server(401);
    const auth = new AuthManager(API, "https://auth.x.test");

    expect((await auth.checkAuth()).status).toBe("unauthenticated");
    auth.markUnauthenticated();
    expect((await auth.checkAuth()).status).toBe("unauthenticated");
    expect(calledUrls()).toEqual([REFRESH]);
  });

  it.each([
    ["typed or bookmarked", ""],
    ["reached from another site", "https://search.example/"],
  ])("is not asked about when the page was %s", async (_label, referrer) => {
    // Not the loop's shape; a signed-out visitor should not pay a refresh.
    vi.spyOn(document, "referrer", "get").mockReturnValue(referrer);
    server(200);
    const auth = new AuthManager(API, "https://auth.x.test");

    expect((await auth.checkAuth()).status).toBe("unauthenticated");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is not asked about on the portal's own host, where signing in rewrites the markers", async () => {
    server(200);
    const auth = new AuthManager(API, `${window.location.origin}/auth`);

    expect((await auth.checkAuth()).status).toBe("unauthenticated");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports an API that did not answer the recovery, not a sign-out", async () => {
    // A deploy or a gateway error is something to wait out; signed out sends a
    // signed-in person to the sign-in page and skips the reconnect retry.
    server(502);
    const failing = new AuthManager(API, "https://auth.x.test");
    expect((await failing.checkAuth()).status).toBe("unreachable");

    clearCookies();
    resetMarkerRecoveryForTests();
    document.cookie = `${MARKER}=1700000000000; path=/`;
    fetchMock.mockReset();
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const offline = new AuthManager(API, "https://auth.x.test");
    expect((await offline.checkAuth()).status).toBe("unreachable");
  });
});
