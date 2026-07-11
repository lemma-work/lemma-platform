import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  challengeForDesktopVerifier,
  clearPendingDesktopAuth,
  createDesktopVerifier,
  getPendingDesktopAuth,
  isLemmaDesktop,
  readDesktopRequestIdFromSearch,
  storePendingDesktopAuth,
} from "./desktop";

describe("desktop auth helpers", () => {
  beforeEach(() => {
    vi.stubGlobal("window", {
      sessionStorage: new MemoryStorage(),
      crypto: globalThis.crypto,
      btoa: globalThis.btoa.bind(globalThis),
    });
    window.sessionStorage.clear();
    delete window.__LEMMA_DESKTOP__;
  });

  afterEach(() => vi.unstubAllGlobals());

  it("detects only an injected desktop shell marker", () => {
    expect(isLemmaDesktop()).toBe(false);
    window.__LEMMA_DESKTOP__ = { version: "0.2.1", mode: "local" };
    expect(isLemmaDesktop()).toBe(true);
  });

  it("accepts only bounded URL-safe request ids", () => {
    expect(
      readDesktopRequestIdFromSearch("?desktop_request=desktop-request-123456789"),
    ).toBe("desktop-request-123456789");
    expect(readDesktopRequestIdFromSearch("?desktop_request=short")).toBeNull();
    expect(readDesktopRequestIdFromSearch("?desktop_request=bad%20request%21")).toBeNull();
  });

  it("drops expired pending requests", () => {
    storePendingDesktopAuth({
      requestId: "desktop-request-123456789",
      verifier: "v".repeat(43),
      browserUrl: "https://lemma.work/auth",
      expiresAt: Date.now() - 1,
    });
    expect(getPendingDesktopAuth()).toBeNull();
    clearPendingDesktopAuth();
  });

  it("creates URL-safe PKCE material", async () => {
    const verifier = createDesktopVerifier();
    const challenge = await challengeForDesktopVerifier(verifier);

    expect(verifier).toHaveLength(43);
    expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });
});

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length() {
    return this.values.size;
  }

  clear() {
    this.values.clear();
  }

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  key(index: number) {
    return [...this.values.keys()][index] ?? null;
  }

  removeItem(key: string) {
    this.values.delete(key);
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}
