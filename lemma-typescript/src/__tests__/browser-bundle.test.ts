import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// The committed IIFE bundle served at /public/sdk/lemma-client.js. The widget /
// no-build-app runtime depends on its global shape, so lock it here.
const bundlePath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../public/lemma-client.js",
);

function loadBundle(): void {
  // The bundle is a strict-mode IIFE assigning `var LemmaClient = (...)()`. Under
  // eval, a strict-mode `var` stays in the eval scope rather than leaking to the
  // global (a real <script> would make it a window property). Append a copy in
  // the same eval scope so we observe the same global shape a browser sees.
  const code = readFileSync(bundlePath, "utf-8");
  (0, eval)(`${code}\n;globalThis.LemmaClient = LemmaClient;`);
}

describe("browser bundle globals", () => {
  beforeEach(() => {
    const g = globalThis as Record<string, unknown>;
    delete g.LemmaClient;
    delete g.Lemma;
    delete (window as unknown as Record<string, unknown>).__LEMMA_CONFIG__;
  });

  it("exposes window.LemmaClient.LemmaClient and the window.Lemma alias", () => {
    loadBundle();
    const g = globalThis as Record<string, any>;
    expect(g.LemmaClient).toBeDefined();
    expect(typeof g.LemmaClient.LemmaClient).toBe("function");
    expect(g.LemmaClient.POD_DEFAULT_AGENT_SELECTOR).toBe("POD_DEFAULT");
    expect(g.LemmaClient.LEMMA_APP_THEME_MESSAGE_TYPE).toBe("lemma-app-theme");
    expect(typeof g.LemmaClient.getLemmaHostTheme).toBe("function");
    expect(typeof g.LemmaClient.subscribeLemmaHostTheme).toBe("function");
    // Back-compat alias for widgets authored against `new Lemma.LemmaClient()`.
    expect(g.Lemma).toBeDefined();
    expect(g.Lemma.LemmaClient).toBe(g.LemmaClient.LemmaClient);
  });

  it("resolves pod context from window.__LEMMA_CONFIG__", () => {
    (window as unknown as Record<string, unknown>).__LEMMA_CONFIG__ = {
      apiUrl: "https://api.example.test/",
      podId: "pod-xyz",
      authUrl: "https://auth.example.test/",
      app: { name: "Support Triage" },
    };
    loadBundle();
    const g = globalThis as Record<string, any>;
    const client = new g.LemmaClient.LemmaClient(); // takes no args; reads the global
    expect(client.podId).toBe("pod-xyz");
    expect(client.apiUrl).toBe("https://api.example.test"); // trailing slash stripped
    expect(client.app).toEqual({ name: "Support Triage" });
  });

  describe("runtime client calls", () => {
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("keeps cross-origin reads simple so the browser can skip a CORS preflight", async () => {
      (window as unknown as Record<string, unknown>).__LEMMA_CONFIG__ = {
        apiUrl: "https://api.example.test",
        podId: "pod-xyz",
        authUrl: "https://auth.example.test",
      };
      loadBundle();

      const fetchMock = vi.fn(
        async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> =>
          new Response(JSON.stringify({ items: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const g = globalThis as Record<string, any>;
      const client = new g.LemmaClient.LemmaClient();
      await client.tables.list();

      expect(fetchMock).toHaveBeenCalled();
      const [url, init] = fetchMock.mock.calls[0];
      expect(String(url)).toContain("/pods/pod-xyz/datastore/tables");
      const headers = new Headers(init?.headers ?? {});
      expect(headers.has("x-lemma-client")).toBe(false);
      expect(headers.has("content-type")).toBe(false);
    });

    it("keeps client identity and JSON headers on cross-origin writes", async () => {
      (window as unknown as Record<string, unknown>).__LEMMA_CONFIG__ = {
        apiUrl: "https://api.example.test",
        podId: "pod-xyz",
        authUrl: "https://auth.example.test",
      };
      loadBundle();

      const fetchMock = vi.fn(
        async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> =>
          new Response(JSON.stringify({}), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const g = globalThis as Record<string, any>;
      const client = new g.LemmaClient.LemmaClient();
      await client.tables.create({ name: "tasks", columns: [] });

      const [, init] = fetchMock.mock.calls[0];
      const headers = new Headers(init?.headers ?? {});
      expect(headers.get("x-lemma-client")).toMatch(/^lemma-sdk-ts\//);
      expect(headers.get("content-type")).toBe("application/json");
    });
  });
});
