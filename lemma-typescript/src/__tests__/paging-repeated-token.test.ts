import { afterEach, describe, expect, it, vi } from "vitest";
import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import { AppsNamespace } from "../namespaces/apps.js";
import { FunctionsNamespace } from "../namespaces/functions.js";
import { RecordsNamespace } from "../namespaces/records.js";
import { AppsService } from "../openapi_client/services/AppsService.js";
import { FunctionsService } from "../openapi_client/services/FunctionsService.js";
import { RecordsService } from "../openapi_client/services/RecordsService.js";

/**
 * The "all pages" walks end only when the server stops sending a cursor. A
 * server that hands back one it already sent would keep them paging -- and
 * accumulating the same rows -- forever, so each must stop on a repeat.
 */

const passthroughAdapter = { request: (op: () => unknown) => op() } as unknown as GeneratedClientAdapter;

afterEach(() => vi.restoreAllMocks());

/** Page 1 points at "a", "a" points at "b", and "b" points back at "a". */
function cyclingPages() {
  const next: Record<string, string> = { "": "a", a: "b", b: "a" };
  return (pageToken?: string | null) =>
    Promise.resolve({ items: [{ id: pageToken ?? "first" }], next_page_token: next[pageToken ?? ""] });
}

describe("paging stops on a repeated page token", () => {
  it("apps.allReleases", async () => {
    const pages = cyclingPages();
    const spy = vi
      .spyOn(AppsService, "appReleaseList")
      .mockImplementation(((_pod: string, _name: string, _limit?: number, token?: string | null) =>
        pages(token)) as never);
    const apps = new AppsNamespace(passthroughAdapter, {} as HttpClient, () => "pod1");

    await expect(apps.allReleases("board")).rejects.toThrow(/repeated page token "a"/);
    expect(spy).toHaveBeenCalledTimes(3);
  });

  it("functions.revisions.listAll", async () => {
    const pages = cyclingPages();
    const spy = vi
      .spyOn(FunctionsService, "functionRevisionList")
      .mockImplementation(((_pod: string, _name: string, _limit?: number, token?: string | null) =>
        pages(token)) as never);
    const functions = new FunctionsNamespace(passthroughAdapter, () => "pod1");

    await expect(functions.revisions.listAll("sync")).rejects.toThrow(/repeated page token "a"/);
    expect(spy).toHaveBeenCalledTimes(3);
  });

  it("records.listAll", async () => {
    const pages = cyclingPages();
    const spy = vi.spyOn(RecordsService, "recordList").mockImplementation(((
      _pod: string,
      _table: string,
      _limit?: number,
      _offset?: number,
      _filters?: string[],
      _sort?: string[],
      token?: string,
    ) => pages(token)) as never);
    const records = new RecordsNamespace(passthroughAdapter, () => "pod1");

    await expect(records.listAll("tickets")).rejects.toThrow(/repeated page token "a"/);
    expect(spy).toHaveBeenCalledTimes(3);
  });

  it("still follows distinct tokens to the end", async () => {
    const next: Record<string, string | null> = { "": "a", a: "b", b: null };
    vi.spyOn(AppsService, "appReleaseList").mockImplementation(((
      _pod: string,
      _name: string,
      _limit?: number,
      token?: string | null,
    ) => Promise.resolve({ items: [{ id: token ?? "first" }], next_page_token: next[token ?? ""] })) as never);
    const apps = new AppsNamespace(passthroughAdapter, {} as HttpClient, () => "pod1");

    await expect(apps.allReleases("board")).resolves.toEqual([{ id: "first" }, { id: "a" }, { id: "b" }]);
  });
});
