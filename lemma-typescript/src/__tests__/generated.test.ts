import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthManager } from "../auth.js";
import { GeneratedClientAdapter } from "../generated.js";
import { NetworkError, NotFoundError } from "../http.js";
import { ApiError as GeneratedApiError } from "../openapi_client/core/ApiError.js";
import { CancelablePromise } from "../openapi_client/core/CancelablePromise.js";

function fakeAuth(): { auth: AuthManager; markUnauthenticated: ReturnType<typeof vi.fn> } {
  const markUnauthenticated = vi.fn();
  const auth = {
    isTokenMode: false,
    getBearerToken: () => null,
    markUnauthenticated,
  } as unknown as AuthManager;
  return { auth, markUnauthenticated };
}

function genError(
  status: number,
  body?: unknown,
  method: string = "GET",
): GeneratedApiError {
  return new GeneratedApiError(
    { method, url: "" } as never,
    { url: "", ok: false, status, statusText: "", body } as never,
    "generated error",
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("GeneratedClientAdapter timeout", () => {
  it("cancels a slow operation after timeoutMs and rejects with NetworkError", async () => {
    vi.useFakeTimers();
    const { auth } = fakeAuth();
    const adapter = new GeneratedClientAdapter("https://api.test", auth, {
      timeoutMs: 1_000,
      maxRetries: 0,
    });

    let cancelled = false;
    const operation = () =>
      new CancelablePromise<unknown>((_resolve, _reject, onCancel) => {
        onCancel(() => {
          cancelled = true;
        });
      });

    const promise = adapter.request(operation);
    const assertion = expect(promise).rejects.toBeInstanceOf(NetworkError);
    await vi.advanceTimersByTimeAsync(1_000);
    await assertion;
    expect(cancelled).toBe(true);
  });

  it("does not time out a fast operation", async () => {
    const { auth } = fakeAuth();
    const adapter = new GeneratedClientAdapter("https://api.test", auth, {
      timeoutMs: 1_000,
      maxRetries: 0,
    });
    const result = await adapter.request(() => Promise.resolve({ ok: true }));
    expect(result).toEqual({ ok: true });
  });
});

describe("GeneratedClientAdapter retry + error mapping", () => {
  it("retries a retryable GeneratedApiError then succeeds", async () => {
    vi.useFakeTimers();
    const { auth } = fakeAuth();
    const adapter = new GeneratedClientAdapter("https://api.test", auth, { maxRetries: 2 });

    const operation = vi
      .fn()
      .mockRejectedValueOnce(genError(503))
      .mockResolvedValueOnce({ ok: true });

    const promise = adapter.request(operation as () => Promise<unknown>);
    await vi.advanceTimersByTimeAsync(2_000);
    await expect(promise).resolves.toEqual({ ok: true });
    expect(operation).toHaveBeenCalledTimes(2);
  });

  it("does not replay a write on a gateway error", async () => {
    // A 504 usually means the handler is still running; replaying the POST is
    // how one create becomes two rows.
    const { auth } = fakeAuth();
    const adapter = new GeneratedClientAdapter("https://api.test", auth, { maxRetries: 2 });

    const operation = vi
      .fn()
      .mockRejectedValueOnce(genError(504, undefined, "POST"))
      .mockResolvedValueOnce({ ok: true });

    await expect(
      adapter.request(operation as () => Promise<unknown>),
    ).rejects.toMatchObject({ statusCode: 504 });
    expect(operation).toHaveBeenCalledTimes(1);
  });

  it("still retries a write on 429, which never reached the handler", async () => {
    vi.useFakeTimers();
    const { auth } = fakeAuth();
    const adapter = new GeneratedClientAdapter("https://api.test", auth, { maxRetries: 2 });

    const operation = vi
      .fn()
      .mockRejectedValueOnce(genError(429, undefined, "POST"))
      .mockResolvedValueOnce({ ok: true });

    const promise = adapter.request(operation as () => Promise<unknown>);
    await vi.advanceTimersByTimeAsync(2_000);
    await expect(promise).resolves.toEqual({ ok: true });
    expect(operation).toHaveBeenCalledTimes(2);
  });

  it("maps a non-retryable GeneratedApiError to the typed ApiError subclass", async () => {
    const { auth } = fakeAuth();
    const adapter = new GeneratedClientAdapter("https://api.test", auth, { maxRetries: 2 });
    const operation = () => Promise.reject(genError(404, { message: "missing", code: "not_found" }));
    await expect(adapter.request(operation)).rejects.toMatchObject({
      statusCode: 404,
      code: "not_found",
    });
    await expect(adapter.request(operation)).rejects.toBeInstanceOf(NotFoundError);
  });

  it("marks the session unauthenticated on a 401 from the generated client", async () => {
    const { auth, markUnauthenticated } = fakeAuth();
    const adapter = new GeneratedClientAdapter("https://api.test", auth, { maxRetries: 0 });
    await expect(adapter.request(() => Promise.reject(genError(401)))).rejects.toBeDefined();
    expect(markUnauthenticated).toHaveBeenCalled();
  });
});
