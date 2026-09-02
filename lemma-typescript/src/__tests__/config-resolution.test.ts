import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  DEFAULT_API_URL,
  resetConfigWarnings,
  resolveConfig,
} from "../config.js";

// The three Lemma clients have to agree on how you name a host. The CLI and the
// Python SDK read LEMMA_BASE_URL; this SDK used to read only LEMMA_API_URL, so
// a Node script that set the documented name silently talked to production.

const ENV_KEYS = ["LEMMA_BASE_URL", "LEMMA_API_URL", "LEMMA_AUTH_URL", "LEMMA_TOKEN"];

describe("base URL resolution", () => {
  beforeEach(() => {
    for (const key of ENV_KEYS) vi.stubEnv(key, "");
    delete (window as unknown as Record<string, unknown>).__LEMMA_CONFIG__;
    resetConfigWarnings();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("reads LEMMA_BASE_URL, the name the CLI and Python SDK use", () => {
    vi.stubEnv("LEMMA_BASE_URL", "http://localhost:8000");

    expect(resolveConfig().apiUrl).toBe("http://localhost:8000");
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("still accepts LEMMA_API_URL, once, with a rename hint", () => {
    vi.stubEnv("LEMMA_API_URL", "http://legacy.test");

    expect(resolveConfig().apiUrl).toBe("http://legacy.test");
    expect(resolveConfig().apiUrl).toBe("http://legacy.test");

    expect(console.warn).toHaveBeenCalledTimes(1);
    expect(vi.mocked(console.warn).mock.calls[0][0]).toContain("LEMMA_BASE_URL");
  });

  it("prefers LEMMA_BASE_URL when both are set", () => {
    vi.stubEnv("LEMMA_BASE_URL", "http://preferred.test");
    vi.stubEnv("LEMMA_API_URL", "http://legacy.test");

    expect(resolveConfig().apiUrl).toBe("http://preferred.test");
  });

  it("lets an explicit override and window config win over the environment", () => {
    vi.stubEnv("LEMMA_BASE_URL", "http://from-env.test");
    (window as unknown as Record<string, unknown>).__LEMMA_CONFIG__ = {
      apiUrl: "http://from-window.test",
    };

    expect(resolveConfig().apiUrl).toBe("http://from-window.test");
    expect(resolveConfig({ apiUrl: "http://explicit.test" }).apiUrl).toBe(
      "http://explicit.test",
    );
  });

  it("says so when a server-side caller falls through to the public default", () => {
    vi.stubGlobal("window", undefined);
    try {
      expect(resolveConfig().apiUrl).toBe(DEFAULT_API_URL);
      expect(vi.mocked(console.warn).mock.calls[0][0]).toContain(
        "LEMMA_BASE_URL",
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("stays quiet in a browser, where the default is the answer", () => {
    expect(resolveConfig().apiUrl).toBe(DEFAULT_API_URL);
    expect(console.warn).not.toHaveBeenCalled();
  });
});
