// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { registerAppAccessFrame, startAppAccess } from "../app-access.js";
import Session from "supertokens-web-js/recipe/session/index.js";

vi.mock("../auth.js", async importOriginal => {
  const actual = await importOriginal<typeof import("../auth.js")>();
  return { ...actual, AuthManager: class {
    getRequestInit(init: RequestInit) { return { ...init, credentials: "include" }; }
    markUnauthenticated() {}
  } };
});
vi.mock("supertokens-web-js/recipe/session/index.js", () => ({ default: { attemptRefreshingSession: vi.fn().mockResolvedValue(false) } }));

const options = { apiUrl: "https://api.example.test", authUrl: "https://workspace.example.test/auth", appOrigin: "https://orders.apps.example.test" };
const requestId = "r".repeat(43);
const code = "c".repeat(43);
let cleanups: Array<() => void> = [];

beforeEach(() => { vi.clearAllMocks(); vi.mocked(Session.attemptRefreshingSession).mockResolvedValue(false); document.body.innerHTML = "<iframe></iframe>"; vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code, expires_in_seconds: 60 }), { headers: { "Content-Type": "application/json" } }))); });
afterEach(() => { for (const cleanup of cleanups) cleanup(); cleanups = []; vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

function send(frame: HTMLIFrameElement, overrides: Partial<MessageEventInit> = {}) {
  window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, origin: options.appOrigin, data: { type: "lemma:app-access:request", requestId }, ...overrides }));
}

describe("app access frame", () => {
  it("authorizes only the registered frame and replies to its exact origin", async () => {
    const frame = document.querySelector("iframe")!;
    const reply = vi.spyOn(frame.contentWindow!, "postMessage");
    cleanups.push(registerAppAccessFrame(frame, options));
    send(frame, { origin: "https://other.apps.example.test" });
    send(frame, { source: window });
    send(frame, { data: { type: "lemma:app-access:request", requestId: "invalid" } });
    expect(fetch).not.toHaveBeenCalled();
    send(frame);
    await vi.waitFor(() => expect(reply).toHaveBeenCalledWith({ type: "lemma:app-access:result", requestId, code }, options.appOrigin));
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining(`/apps/access/requests/${requestId}/authorize`), expect.objectContaining({ credentials: "include", body: JSON.stringify({ app_origin: options.appOrigin }) }));
  });

  it("ignores repeated offers and stops authorizing after teardown", async () => {
    const frame = document.querySelector("iframe")!;
    const cleanup = registerAppAccessFrame(frame, options);
    cleanups.push(cleanup);
    send(frame); send(frame);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    cleanup();
    send(frame, { data: { type: "lemma:app-access:request", requestId: "s".repeat(43) } });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("reports permission denial without handing back a credential", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ message: "Not found" }), { status: 404, headers: { "Content-Type": "application/json" } }));
    const frame = document.querySelector("iframe")!;
    const reply = vi.spyOn(frame.contentWindow!, "postMessage");
    cleanups.push(registerAppAccessFrame(frame, options));
    send(frame);
    await vi.waitFor(() => expect(reply).toHaveBeenCalledWith(expect.objectContaining({ error: "denied" }), options.appOrigin));
    expect(reply.mock.calls[0][0]).not.toHaveProperty("code");
  });

  it("does not refresh the main session for an expired or forged handoff", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ message: "Expired", code: "APP_ACCESS_INVALID" }), { status: 401, headers: { "Content-Type": "application/json" } }));
    const frame = document.querySelector("iframe")!;
    const reply = vi.spyOn(frame.contentWindow!, "postMessage");
    cleanups.push(registerAppAccessFrame(frame, options));
    send(frame);
    await vi.waitFor(() => expect(reply).toHaveBeenCalledWith(expect.objectContaining({ error: "unavailable" }), options.appOrigin));
    expect(Session.attemptRefreshingSession).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("returns a sign-in action after one unsuccessful session refresh", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ message: "Sign in" }), { status: 401, headers: { "Content-Type": "application/json" } }));
    const frame = document.querySelector("iframe")!;
    const reply = vi.spyOn(frame.contentWindow!, "postMessage");
    cleanups.push(registerAppAccessFrame(frame, options));
    send(frame);
    await vi.waitFor(() => expect(reply).toHaveBeenCalledWith(expect.objectContaining({ error: "signed-out" }), options.appOrigin));
    const destination = new URL(reply.mock.calls[0][0].signInUrl);
    expect(destination.origin).toBe(new URL(options.authUrl).origin);
    expect(destination.searchParams.get("redirect_uri")).toBe(window.location.href);
    expect(Session.attemptRefreshingSession).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("shows an actionable error when session refresh stops answering", async () => {
    vi.useFakeTimers();
    vi.mocked(Session.attemptRefreshingSession).mockImplementation(() => new Promise(() => {}));
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ message: "Sign in" }), { status: 401, headers: { "Content-Type": "application/json" } }));
    const frame = document.querySelector("iframe")!;
    const reply = vi.spyOn(frame.contentWindow!, "postMessage");
    cleanups.push(registerAppAccessFrame(frame, options));
    send(frame);
    await vi.advanceTimersByTimeAsync(10_001);
    expect(reply).toHaveBeenCalledWith(expect.objectContaining({ error: "unavailable" }), options.appOrigin);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not reload when the browser rejects the app cookie", async () => {
    document.body.innerHTML = '<p id="app-access-status"></p><a id="app-access-sign-in"></a><button id="app-access-retry"></button>';
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({ request_id: requestId })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code, expires_in_seconds: 60 }), { headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ expires_at: Date.now() })))
      .mockResolvedValueOnce(new Response("", { status: 401 }));
    await startAppAccess({ ...options, parentOrigin: "https://workspace.example.test" });
    expect(document.getElementById("app-access-status")?.textContent).toContain("blocked app access");
    expect(fetch).toHaveBeenCalledTimes(4);
  });
});
