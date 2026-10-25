import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthManager } from "../auth.js";
import { HttpClient, noteDeprecation } from "../http.js";
import { ConnectorsNamespace } from "../namespaces/connectors.js";

/**
 * Addressing a connector by installation and account predates pod connectors.
 * Live apps still do it, so the server keeps serving the org-level execute and
 * marks every response with `X-Lemma-Deprecation`; the client logs that once.
 */

function fakeAuth(): AuthManager {
  return {
    getRequestInit: (init: RequestInit = {}) => init,
    markUnauthenticated: vi.fn(),
    ready: async () => undefined,
    renewEmbeddedToken: async () => false,
  } as unknown as AuthManager;
}

function jsonResponse(body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json", ...headers },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("noteDeprecation", () => {
  it("warns once per message, and again for a different one", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const notice = (message: string) => new Headers({ "X-Lemma-Deprecation": message });

    noteDeprecation(notice("once.a is deprecated: use b"));
    noteDeprecation(notice("once.a is deprecated: use b"));
    noteDeprecation(notice("once.c is deprecated: use d"));
    noteDeprecation(new Headers({ Deprecation: "true" }));

    expect(warn.mock.calls).toEqual([
      ["[lemma] once.a is deprecated: use b"],
      ["[lemma] once.c is deprecated: use d"],
    ]);
  });

  it("is noted on a successful request, which still returns its body", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ ok: true }, { "X-Lemma-Deprecation": "request.x is deprecated: y" }),
      ),
    );
    const http = new HttpClient("https://api.test", fakeAuth(), { maxRetries: 0 });

    await expect(http.request("GET", "/x")).resolves.toEqual({ ok: true });
    expect(warn).toHaveBeenCalledWith("[lemma] request.x is deprecated: y");
  });
});

describe("connectors.operations.execute (deprecated)", () => {
  function namespace(podId: () => string) {
    const request = vi.fn().mockResolvedValue({ result: { ok: true } });
    const connectors = new ConnectorsNamespace(
      { request: (fn: () => unknown) => fn() } as never,
      { request } as never,
      podId,
    );
    return { connectors, request };
  }

  it("posts to the org-level route with the account and the client's pod", async () => {
    const { connectors, request } = namespace(() => "pod-1");

    const result = await connectors.operations.execute(
      { organizationId: "org-1", authConfigName: "workspace-gmail" },
      "GMAIL_SEND_EMAIL",
      { to: "a@b.c" },
      "acct-1",
    );

    expect(result).toEqual({ result: { ok: true } });
    expect(request).toHaveBeenCalledWith(
      "POST",
      "/organizations/org-1/connectors/workspace-gmail/operations/GMAIL_SEND_EMAIL/execute",
      { body: { payload: { to: "a@b.c" }, account_id: "acct-1", pod_id: "pod-1" } },
    );
  });

  it("takes the scope's pod over the client's, and needs neither", async () => {
    const scoped = namespace(() => "pod-1");
    await scoped.connectors.operations.execute(
      { organizationId: "org-1", authConfigName: "gmail", podId: "pod-2" },
      "LIST",
      {},
    );
    expect(scoped.request.mock.calls[0]?.[2]).toEqual({
      body: { payload: {}, pod_id: "pod-2" },
    });

    // An org-level client has no pod; the call is the person's own.
    const orgOnly = namespace(() => {
      throw new Error("no pod");
    });
    await orgOnly.connectors.operations.execute(
      { organizationId: "org 1", authConfigName: "gmail/a" },
      "LIST",
      {},
    );
    expect(orgOnly.request.mock.calls[0]?.slice(1)).toEqual([
      "/organizations/org%201/connectors/gmail%2Fa/operations/LIST/execute",
      { body: { payload: {} } },
    ]);
  });
});
