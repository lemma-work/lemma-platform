import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthManager } from "../auth.js";
import { AuthManager as RealAuthManager, clearTestingToken, setTestingToken } from "../auth.js";
import {
  HttpClient,
  NetworkError,
  RateLimitError,
  UnauthorizedError,
} from "../http.js";

// Minimal AuthManager double — HttpClient only calls getRequestInit + markUnauthenticated.
function fakeAuth(): { auth: AuthManager; markUnauthenticated: ReturnType<typeof vi.fn> } {
  const markUnauthenticated = vi.fn();
  const auth = {
    getRequestInit: (init: RequestInit = {}) => ({
      ...init,
      headers: { ...(init.headers as Record<string, string>), Authorization: "Bearer test" },
    }),
    markUnauthenticated,
  } as unknown as AuthManager;
  return { auth, markUnauthenticated };
}

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

afterEach(() => {
  clearTestingToken();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("HttpClient.request retry loop", () => {
  it("preserves empty query values", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const { auth } = fakeAuth();
    const client = new HttpClient("https://api.test", auth, { maxRetries: 0 });
    await client.request("GET", "/pods/pod-1/conversations", {
      params: { agent_name: "" },
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://api.test/pods/pod-1/conversations?agent_name=",
    );
  });

  it("preserves bearer auth and the browser multipart boundary for FormData", async () => {
    setTestingToken("TESTTOKEN");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const auth = new RealAuthManager("https://api.test", "https://auth.test");
    const client = new HttpClient("https://api.test", auth, { maxRetries: 0 });
    const form = new FormData();
    form.append("data", new Blob(["bundle"]), "bundle.zip");

    await client.request("POST", "/upload", { body: form, isFormData: true });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(init.body).toBe(form);
    expect(headers.get("authorization")).toBe("Bearer TESTTOKEN");
    expect(headers.has("content-type")).toBe(false);
    expect(headers.get("x-lemma-client")).toMatch(/^lemma-sdk-ts\//);
  });

  it("retries a 503 then returns the success body", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("", { status: 503 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const { auth } = fakeAuth();
    const client = new HttpClient("https://api.test", auth, { maxRetries: 2 });
    const promise = client.request<{ ok: boolean }>("GET", "/x");
    await vi.advanceTimersByTimeAsync(2_000); // flush jittered backoff sleep
    await expect(promise).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("honors Retry-After (seconds) before retrying", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("", { status: 429, headers: { "retry-after": "1" } }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const { auth } = fakeAuth();
    const client = new HttpClient("https://api.test", auth, { maxRetries: 2 });
    const promise = client.request("GET", "/x");

    // Not yet retried at 999ms; the second call lands only after the 1s advice.
    await vi.advanceTimersByTimeAsync(999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await promise;
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops after maxRetries and throws the typed error", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message: "rate limited" }, 429),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { auth } = fakeAuth();
    const client = new HttpClient("https://api.test", auth, { maxRetries: 1 });
    const promise = client.request("GET", "/x");
    const assertion = expect(promise).rejects.toBeInstanceOf(RateLimitError);
    await vi.advanceTimersByTimeAsync(5_000);
    await assertion;
    expect(fetchMock).toHaveBeenCalledTimes(2); // initial + 1 retry
  });

  it("marks the session unauthenticated on 401", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ message: "nope" }, 401));
    vi.stubGlobal("fetch", fetchMock);

    const { auth, markUnauthenticated } = fakeAuth();
    const client = new HttpClient("https://api.test", auth, { maxRetries: 0 });
    await expect(client.request("GET", "/x")).rejects.toBeInstanceOf(UnauthorizedError);
    expect(markUnauthenticated).toHaveBeenCalledOnce();
  });

  it("carries the request id from x-request-id onto the error", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message: "boom" }, 400, { "x-request-id": "req-123" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { auth } = fakeAuth();
    const client = new HttpClient("https://api.test", auth, { maxRetries: 0 });
    await expect(client.request("GET", "/x")).rejects.toMatchObject({ requestId: "req-123" });
  });
});

describe("HttpClient timeout + transport errors", () => {
  it("aborts and surfaces a NetworkError when the request exceeds timeoutMs", async () => {
    vi.useFakeTimers();
    // Reject only when the (timeout) AbortController fires — mimics real fetch.
    const fetchMock = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          (init.signal as AbortSignal).addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { auth } = fakeAuth();
    const client = new HttpClient("https://api.test", auth, { timeoutMs: 1_000, maxRetries: 0 });
    const promise = client.request("GET", "/x");
    const assertion = expect(promise).rejects.toThrow(/timed out after 1000ms/);
    await vi.advanceTimersByTimeAsync(1_000);
    await assertion;
  });

  it("wraps a raw transport failure in NetworkError", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    const { auth } = fakeAuth();
    const client = new HttpClient("https://api.test", auth, { maxRetries: 0 });
    await expect(client.request("GET", "/x")).rejects.toBeInstanceOf(NetworkError);
  });

  it("normalizes a transport failure on the stream() path too", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    const { auth } = fakeAuth();
    const client = new HttpClient("https://api.test", auth, { maxRetries: 0 });
    await expect(client.stream("/events")).rejects.toBeInstanceOf(NetworkError);
  });
});
