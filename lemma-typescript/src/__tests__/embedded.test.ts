// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthManager } from "../auth.js";
import { HttpClient } from "../http.js";
import { startAppAccess } from "../app-access.js";
import {
  ACCESS_MESSAGE,
  ACCESS_REQUEST,
  EmbeddedCredentials,
  TOKEN_MESSAGE,
  TOKEN_REQUEST,
  isEmbeddedInHost,
} from "../embedded.js";

const API = "https://api.example.test";

/**
 * The Lemma view around a framed app: `window.parent`, answering each question
 * the way `pod_mcp_app_view.html` does -- to the frame, by the question's id.
 */
function lemmaView(answer: (request: { type: string; id: string }) => Record<string, unknown> | null) {
  const asked: Array<{ type: string; id: string }> = [];
  const parent = {
    postMessage: (request: { type: string; id: string }) => {
      asked.push(request);
      const reply = answer(request);
      if (reply === null) return;
      queueMicrotask(() =>
        window.dispatchEvent(
          new MessageEvent("message", { data: { id: request.id, ...reply }, source: window.parent as Window }),
        ),
      );
    },
  };
  Object.defineProperty(window, "parent", { value: parent, configurable: true });
  return asked;
}

function tokens(...issued: string[]) {
  let next = 0;
  return lemmaView((request) =>
    request.type === TOKEN_REQUEST
      ? { type: TOKEN_MESSAGE, token: issued[Math.min(next++, issued.length - 1)], expiresAt: new Date(Date.now() + 300_000).toISOString() }
      : null,
  );
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/?lemma_embed=mcp");
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  Object.defineProperty(window, "parent", { value: window, configurable: true });
  window.history.replaceState({}, "", "/");
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("isEmbeddedInHost", () => {
  it("is true only for a frame the Lemma view marked", () => {
    lemmaView(() => null);
    expect(isEmbeddedInHost()).toBe(true);
  });

  it("is false in a frame nobody marked -- the Lemma workspace frames apps too", () => {
    lemmaView(() => null);
    window.history.replaceState({}, "", "/orders");
    expect(isEmbeddedInHost()).toBe(false);
  });

  it("is false for a page that is not framed, whatever its address says", () => {
    expect(isEmbeddedInHost()).toBe(false);
  });

  it("stays true after the app routes away from the address it opened at", () => {
    lemmaView(() => null);
    expect(isEmbeddedInHost()).toBe(true);
    window.history.replaceState({}, "", "/orders/42");
    expect(isEmbeddedInHost()).toBe(true);
  });
});

describe("a framed app's requests", () => {
  it("wait for the view's token and carry it instead of a cookie", async () => {
    const asked = tokens("first");
    vi.mocked(fetch).mockResolvedValueOnce(json({ items: [] }));
    const http = new HttpClient(API, new AuthManager(API, `${API}/auth`));

    await http.request("GET", "/pods/p/datastore/tables");

    expect(asked.map((r) => r.type)).toEqual([TOKEN_REQUEST]);
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(init).toMatchObject({ credentials: "omit", headers: { Authorization: "Bearer first" } });
  });

  it("ask the view once more after a 401, and retry with the new token", async () => {
    tokens("stale", "fresh");
    vi.mocked(fetch).mockResolvedValueOnce(json({ message: "expired" }, 401)).mockResolvedValueOnce(json({ ok: true }));
    const auth = new AuthManager(API, `${API}/auth`);
    const http = new HttpClient(API, auth);

    await expect(http.request("GET", "/users/me")).resolves.toEqual({ ok: true });

    const sent = vi.mocked(fetch).mock.calls.map(([, init]) => (init as RequestInit).headers);
    expect(sent).toEqual([
      expect.objectContaining({ Authorization: "Bearer stale" }),
      expect.objectContaining({ Authorization: "Bearer fresh" }),
    ]);
    expect(auth.getState().status).not.toBe("unauthenticated");
  });

  it("give up after one renewal: a second 401 is the answer", async () => {
    tokens("a", "b");
    vi.mocked(fetch).mockResolvedValue(json({ message: "no" }, 401));
    const auth = new AuthManager(API, `${API}/auth`);

    await expect(new HttpClient(API, auth).request("GET", "/users/me")).rejects.toThrow();
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
    expect(auth.getState().status).toBe("unauthenticated");
  });
});

describe("EmbeddedCredentials", () => {
  it("shares one question among callers asking at once", async () => {
    const asked = tokens("only");
    const credentials = new EmbeddedCredentials();
    await expect(Promise.all([credentials.ready(), credentials.ready(), credentials.renew()])).resolves.toEqual(["only", "only", "only"]);
    expect(asked).toHaveLength(1);
  });

  it("asks for the next token a minute before this one stops working", async () => {
    vi.useFakeTimers();
    const asked = tokens("now", "later");
    const credentials = new EmbeddedCredentials();
    await credentials.ready();
    await vi.advanceTimersByTimeAsync(239_000);
    expect(asked).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(asked).toHaveLength(2);
    expect(credentials.current()).toBe("later");
  });

  it("listens to its own parent only", async () => {
    vi.useFakeTimers();
    lemmaView(() => null);
    const credentials = new EmbeddedCredentials();
    const outcome = credentials.ready().catch((error: Error) => error.message);
    window.dispatchEvent(new MessageEvent("message", { data: { type: TOKEN_MESSAGE, id: "anything", token: "forged" }, source: window as Window }));
    await vi.advanceTimersByTimeAsync(21_000);
    await expect(outcome).resolves.toBe("The Lemma view did not answer");
    expect(credentials.current()).toBeNull();
  });
});

describe("a private app's sign-in page, framed", () => {
  const reload = vi.fn();

  beforeEach(() => {
    reload.mockClear();
    document.body.innerHTML = '<h1 id="app-access-title"></h1><p id="app-access-status"></p><p id="app-access-host"></p><a id="app-access-sign-in"></a><button id="app-access-retry"></button><a id="app-access-home"></a>';
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, href: window.location.href, reload } as Location);
  });

  it("asks the view for a ticket and redeems it for a cookie made for the frame", async () => {
    const asked = lemmaView((request) =>
      request.type === ACCESS_REQUEST ? { type: ACCESS_MESSAGE, ticket: "from-the-connection" } : null,
    );
    vi.mocked(fetch).mockResolvedValueOnce(json({ expires_in_seconds: 43200 })).mockResolvedValueOnce(new Response("ok"));

    await startAppAccess({ apiUrl: API, authUrl: `${API}/auth` });

    expect(asked.map((r) => r.type)).toEqual([ACCESS_REQUEST]);
    const [redeemUrl, redeemInit] = vi.mocked(fetch).mock.calls[0];
    expect(redeemUrl).toBe("/_lemma/app-access/redeem");
    expect(JSON.parse(String((redeemInit as RequestInit).body))).toEqual({ ticket: "from-the-connection", embedded: true });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("says the app is not shared when the view refuses", async () => {
    lemmaView((request) =>
      request.type === ACCESS_REQUEST ? { type: ACCESS_MESSAGE, error: "No app by that name can be opened here." } : null,
    );

    await startAppAccess({ apiUrl: API, authUrl: `${API}/auth` });

    expect(document.body.dataset.state).toBe("denied");
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    expect(reload).not.toHaveBeenCalled();
  });
});
