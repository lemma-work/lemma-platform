// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startAppAccess } from "../app-access.js";
import Session from "supertokens-web-js/recipe/session/index.js";

vi.mock("../auth.js", async importOriginal => {
  const actual = await importOriginal<typeof import("../auth.js")>();
  return { ...actual, AuthManager: class {
    getRequestInit(init: RequestInit) { return { ...init, credentials: "include" }; }
    markUnauthenticated() {}
    async ready() {}
    async renewEmbeddedToken() { return false; }
  } };
});
vi.mock("supertokens-web-js/recipe/session/index.js", () => ({ default: { attemptRefreshingSession: vi.fn().mockResolvedValue(false) } }));

const options = { apiUrl: "https://api.example.test", authUrl: "https://workspace.example.test/auth" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const issued = () => json({ ticket: "signed-ticket", expires_in_seconds: 60 });
const refused = (status: number) => json({ message: "No" }, status);
const reload = vi.fn();

const statusText = () => document.getElementById("app-access-status")?.textContent;
const titleText = () => document.getElementById("app-access-title")?.textContent;
const home = () => document.getElementById("app-access-home") as HTMLAnchorElement;
const signIn = () => document.getElementById("app-access-sign-in") as HTMLAnchorElement;
const retry = () => document.getElementById("app-access-retry") as HTMLButtonElement;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(Session.attemptRefreshingSession).mockResolvedValue(false);
  document.body.innerHTML = '<h1 id="app-access-title"></h1><p id="app-access-status"></p><p id="app-access-host"></p><a id="app-access-sign-in"></a><button id="app-access-retry"></button><a id="app-access-home"></a>';
  vi.stubGlobal("fetch", vi.fn());
  vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, href: window.location.href, reload } as Location);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("app access sign-in page", () => {
  it("trades a ticket for this host's cookie, confirms it, then reloads", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(issued())
      .mockResolvedValueOnce(json({ expires_in_seconds: 43200 }))
      .mockResolvedValueOnce(new Response("ok"));
    await startAppAccess(options);

    const [ticketUrl, ticketInit] = vi.mocked(fetch).mock.calls[0];
    expect(String(ticketUrl)).toContain("https://api.example.test/apps/access/tickets");
    expect(ticketInit).toMatchObject({ method: "POST", credentials: "include" });
    const [redeemUrl, redeemInit] = vi.mocked(fetch).mock.calls[1];
    expect(redeemUrl).toBe("/_lemma/app-access/redeem");
    expect(redeemInit).toMatchObject({ method: "POST", credentials: "same-origin", body: JSON.stringify({ ticket: "signed-ticket" }) });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("offers sign-in, returning here, once a session refresh fails", async () => {
    vi.mocked(fetch).mockResolvedValue(refused(401));
    await startAppAccess({ ...options, authUrl: "https://workspace.example.test" });

    const destination = new URL(signIn().href);
    expect(destination.origin).toBe("https://workspace.example.test");
    expect(destination.pathname).toBe("/auth");
    expect(destination.searchParams.get("redirect_uri")).toBe(window.location.href);
    expect(signIn().hidden).toBe(false);
    expect(signIn().target).toBe("_self");
    expect(retry().hidden).toBe(true);
    expect(titleText()).toBe("Sign in to open this app");
    expect(document.body.dataset.state).toBe("signed-out");
    expect(document.getElementById("app-access-host")?.textContent).toBe(window.location.host);
    expect(Session.attemptRefreshingSession).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("asks again after a successful refresh", async () => {
    vi.mocked(Session.attemptRefreshingSession).mockResolvedValue(true);
    vi.mocked(fetch)
      .mockResolvedValueOnce(refused(401))
      .mockResolvedValueOnce(issued())
      .mockResolvedValueOnce(json({ expires_in_seconds: 43200 }))
      .mockResolvedValueOnce(new Response("ok"));
    await startAppAccess(options);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("asks the server, not a stale marker, whether a lapsed session can be renewed", async () => {
    // A failed refresh left the update marker on this host and took the front
    // token; SuperTokens answers "no session" from that without a request.
    document.cookie = "st-last-access-token-update=1700000000000; path=/";
    const seen: string[] = [];
    vi.mocked(Session.attemptRefreshingSession).mockImplementation(async () => {
      seen.push(document.cookie);
      return true;
    });
    vi.mocked(fetch)
      .mockResolvedValueOnce(refused(401))
      .mockResolvedValueOnce(issued())
      .mockResolvedValueOnce(json({ expires_in_seconds: 43200 }))
      .mockResolvedValueOnce(new Response("ok"));
    await startAppAccess(options);
    expect(seen).toEqual([expect.not.stringContaining("st-last-access-token-update")]);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("signs in at the top when shown in a workspace tab", async () => {
    vi.spyOn(window, "parent", "get").mockReturnValue({} as Window);
    vi.mocked(fetch).mockResolvedValue(refused(401));
    await startAppAccess(options);
    expect(signIn().target).toBe("_top");
  });

  it("says the app is not shared with this account, and offers the way home", async () => {
    vi.mocked(fetch).mockResolvedValue(refused(404));
    await startAppAccess({ ...options, homeUrl: "https://workspace.example.test/" });
    expect(titleText()).toBe("This app isn’t shared with you");
    expect(signIn().hidden).toBe(true);
    expect(home().hidden).toBe(false);
    expect(home().href).toBe("https://workspace.example.test/");
    expect(reload).not.toHaveBeenCalled();
  });

  it("treats a refused redemption as something to retry, not a sign-out", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(issued()).mockResolvedValueOnce(refused(401));
    await startAppAccess(options);
    expect(titleText()).toBe("We couldn’t check your access");
    expect(retry().hidden).toBe(false);
    expect(signIn().hidden).toBe(true);
  });

  it("shows an actionable error when session refresh stops answering", async () => {
    vi.useFakeTimers();
    vi.mocked(Session.attemptRefreshingSession).mockImplementation(() => new Promise(() => {}));
    vi.mocked(fetch).mockResolvedValue(refused(401));
    const started = startAppAccess(options);
    await vi.advanceTimersByTimeAsync(10_001);
    await started;
    expect(titleText()).toBe("We couldn’t check your access");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not reload when the browser rejects the app cookie", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(issued())
      .mockResolvedValueOnce(json({ expires_in_seconds: 43200 }))
      .mockResolvedValueOnce(new Response("", { status: 401 }));
    await startAppAccess(options);
    expect(titleText()).toBe("Your browser blocked app access");
    expect(statusText()).toBe("Allow cookies for this site, then try again.");
    expect(retry().hidden).toBe(false);
    expect(reload).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
