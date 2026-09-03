import { describe, expect, it } from "vitest";
import {
  apiErrorFromStatus,
  ApiError,
  UnauthorizedError,
  ForbiddenError,
  NotFoundError,
  ConflictError,
  RateLimitError,
  ServerError,
} from "../http.js";
import {
  RETRYABLE_STATUS,
  isRetryableRequest,
  retryAfterMs,
  nextBackoffDelay,
} from "../run-utils.js";

describe("apiErrorFromStatus", () => {
  it("maps statuses to the most specific subclass", () => {
    expect(apiErrorFromStatus(401, "x")).toBeInstanceOf(UnauthorizedError);
    expect(apiErrorFromStatus(403, "x")).toBeInstanceOf(ForbiddenError);
    expect(apiErrorFromStatus(404, "x")).toBeInstanceOf(NotFoundError);
    expect(apiErrorFromStatus(409, "x")).toBeInstanceOf(ConflictError);
    expect(apiErrorFromStatus(429, "x")).toBeInstanceOf(RateLimitError);
    expect(apiErrorFromStatus(503, "x")).toBeInstanceOf(ServerError);
  });

  it("falls back to the base ApiError for unmapped statuses", () => {
    expect(apiErrorFromStatus(418, "x").constructor).toBe(ApiError);
  });

  it("keeps every subclass an instanceof ApiError (catch(ApiError) still works)", () => {
    expect(apiErrorFromStatus(404, "x")).toBeInstanceOf(ApiError);
  });

  it("carries retryAfterMs on rate-limit errors", () => {
    const err = apiErrorFromStatus(429, "x", undefined, undefined, undefined, 1500) as RateLimitError;
    expect(err.retryAfterMs).toBe(1500);
  });
});

describe("retry policy", () => {
  it("retries gateway/rate-limit but not 500", () => {
    expect(RETRYABLE_STATUS.has(429)).toBe(true);
    expect(RETRYABLE_STATUS.has(503)).toBe(true);
    expect(RETRYABLE_STATUS.has(500)).toBe(false);
  });

  it("replays a gateway error only for a method that carries no side effect", () => {
    expect(isRetryableRequest(503, "GET")).toBe(true);
    expect(isRetryableRequest(504, "HEAD")).toBe(true);
    expect(isRetryableRequest(504, "POST")).toBe(false);
    expect(isRetryableRequest(502, "PATCH")).toBe(false);
    // A replayed DELETE that in fact succeeded reports 404, which reads worse
    // than the gateway error it replaced.
    expect(isRetryableRequest(503, "DELETE")).toBe(false);
    // An unknown method is assumed to write.
    expect(isRetryableRequest(503, undefined)).toBe(false);
  });

  it("replays 429 whatever the method: the handler never ran", () => {
    expect(isRetryableRequest(429, "POST")).toBe(true);
    expect(isRetryableRequest(429, undefined)).toBe(true);
    expect(isRetryableRequest(500, "GET")).toBe(false);
  });

  it("honors Retry-After seconds, caps at 30s, and falls back to backoff", () => {
    expect(retryAfterMs("2", 0)).toBe(2000);
    expect(retryAfterMs("9999999", 0)).toBe(30000);
    expect(retryAfterMs(null, 0)).toBe(nextBackoffDelay(0));
  });
});
