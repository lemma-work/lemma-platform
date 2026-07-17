import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthManager, clearTestingToken, resolveSafeRedirectUri, setTestingToken } from "../auth.js";

const siteOrigin = "https://app.lemma.work";

// Keep SuperTokens init a no-op; we only care about the session gate.
vi.mock("../supertokens.js", () => ({
  ensureCookieSessionSupport: vi.fn(),
}));

const doesSessionExist = vi.fn<() => Promise<boolean>>();
vi.mock("supertokens-web-js/recipe/session", () => ({
  default: {
    doesSessionExist: () => doesSessionExist(),
    getAccessToken: vi.fn(),
    attemptRefreshingSession: vi.fn(),
    signOut: vi.fn(),
  },
}));

describe("AuthManager.checkAuth cookie-mode session gate", () => {
  afterEach(() => {
    clearTestingToken();
    vi.restoreAllMocks();
    doesSessionExist.mockReset();
  });

  it("short-circuits to unauthenticated without hitting the network when no local session exists", async () => {
    doesSessionExist.mockResolvedValue(false);
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const auth = new AuthManager("https://api.x.test", "https://auth.x.test");
    const state = await auth.checkAuth();

    expect(state.status).toBe("unauthenticated");
    expect(doesSessionExist).toHaveBeenCalledTimes(1);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("calls /users/me when a local session exists", async () => {
    doesSessionExist.mockResolvedValue(true);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "u1", email: "a@b.c" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    const auth = new AuthManager("https://api.x.test", "https://auth.x.test");
    const state = await auth.checkAuth();

    expect(state.status).toBe("authenticated");
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe("https://api.x.test/users/me");
    const headers = new Headers(fetchSpy.mock.calls[0][1]?.headers);
    expect(headers.get("accept")).toBe("application/json");
    expect(headers.has("content-type")).toBe(false);
  });

  it("coalesces concurrent auth checks into one session check and one request", async () => {
    let resolveSessionCheck: ((exists: boolean) => void) | undefined;
    doesSessionExist.mockReturnValue(
      new Promise<boolean>((resolve) => {
        resolveSessionCheck = resolve;
      }),
    );
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "u1", email: "a@b.c" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    const auth = new AuthManager("https://api.x.test", "https://auth.x.test");
    const first = auth.checkAuth();
    const second = auth.checkAuth();

    expect(first).toBe(second);
    expect(doesSessionExist).toHaveBeenCalledTimes(1);
    resolveSessionCheck?.(true);
    await expect(Promise.all([first, second])).resolves.toHaveLength(2);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("bypasses the session gate in injected-token mode", async () => {
    setTestingToken("TESTTOKEN");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "u1", email: "a@b.c" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    const auth = new AuthManager("https://api.x.test", "https://auth.x.test");
    const state = await auth.checkAuth();

    expect(state.status).toBe("authenticated");
    expect(doesSessionExist).not.toHaveBeenCalled();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});

describe("AuthManager request headers", () => {
  afterEach(() => {
    clearTestingToken();
  });

  it("keeps the public headers value plain while normalizing supported HeadersInit inputs", () => {
    setTestingToken("TESTTOKEN");
    const auth = new AuthManager("https://api.x.test", "https://auth.x.test");
    const init = auth.getRequestInit({
      method: "POST",
      body: JSON.stringify({ ok: true }),
      headers: new Headers({ "X-Custom": "present" }),
    });

    expect(init.headers).not.toBeInstanceOf(Headers);
    expect(Object.getPrototypeOf(init.headers as object)).toBe(Object.prototype);
    expect(init.headers).toMatchObject({
      Accept: "application/json",
      Authorization: "Bearer TESTTOKEN",
      "Content-Type": "application/json",
    });
    const headers = new Headers(init.headers);
    expect(headers.get("accept")).toBe("application/json");
    expect(headers.get("authorization")).toBe("Bearer TESTTOKEN");
    expect(headers.get("content-type")).toBe("application/json");
    expect(headers.get("x-custom")).toBe("present");
    expect(init.credentials).toBe("omit");
  });
});

describe("resolveSafeRedirectUri", () => {
  it("resolves relative redirects against the site origin", () => {
    expect(resolveSafeRedirectUri("/pod/p1", { siteOrigin })).toBe("https://app.lemma.work/pod/p1");
  });

  it("allows same-origin absolute redirects", () => {
    expect(resolveSafeRedirectUri("https://app.lemma.work/pods", { siteOrigin })).toBe(
      "https://app.lemma.work/pods",
    );
  });

  it("blocks cross-origin redirects by default", () => {
    expect(resolveSafeRedirectUri("https://evil.example/steal", { siteOrigin })).toBe(
      "https://app.lemma.work/",
    );
  });

  it("blocks local auth paths to avoid redirect loops", () => {
    expect(resolveSafeRedirectUri("/auth/callback", { siteOrigin, fallback: "/home" })).toBe(
      "https://app.lemma.work/home",
    );
  });

  it("allows configured exact origins and hostname suffixes", () => {
    expect(
      resolveSafeRedirectUri("https://trusted.example/continue", {
        siteOrigin,
        allowedOrigins: ["https://trusted.example"],
      }),
    ).toBe("https://trusted.example/continue");

    expect(
      resolveSafeRedirectUri("https://sales.apps.lemma.work/app", {
        siteOrigin,
        allowedOriginSuffixes: ["apps.lemma.work"],
      }),
    ).toBe("https://sales.apps.lemma.work/app");
  });

  it("does not confuse hostname suffixes with lookalike hosts", () => {
    expect(
      resolveSafeRedirectUri("https://evilapps.lemma.work/app", {
        siteOrigin,
        allowedOriginSuffixes: ["apps.lemma.work"],
      }),
    ).toBe("https://app.lemma.work/");
  });

  it("requires https for allowed suffix redirects when the site is https", () => {
    expect(
      resolveSafeRedirectUri("http://sales.apps.lemma.work/app", {
        siteOrigin,
        allowedOriginSuffixes: ["apps.lemma.work"],
      }),
    ).toBe("https://app.lemma.work/");
  });

  it("allows loopback only when explicitly requested", () => {
    expect(resolveSafeRedirectUri("http://127.0.0.1:49152/callback", { siteOrigin })).toBe(
      "https://app.lemma.work/",
    );
    expect(
      resolveSafeRedirectUri("http://127.0.0.1:49152/callback", {
        siteOrigin,
        allowLoopback: true,
      }),
    ).toBe("http://127.0.0.1:49152/callback");
  });
});
