import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/sdk/lemma-client", () => ({
  getLemmaApiBaseUrl: () => "http://app.127.0.0.1.sslip.io:53664/",
}));

import { readFileSync } from "node:fs";

import {
  POD_FILE_DOWNLOAD_ROUTE,
  isPodFilePath,
  podFileBrowserHref,
  podFileDownloadHref,
} from "./assistant-pod-image";

describe("agent-produced image paths", () => {
  it("treats a pod path as a pod path", () => {
    // What the Agent Host artifact writer actually emits.
    expect(isPodFilePath("/me/c/2026-09-22/portraits/agent-output/a.png")).toBe(true);
  });

  it("leaves anything already addressable alone", () => {
    // These resolve on their own; rewriting them would break them.
    expect(isPodFilePath("https://example.com/a.png")).toBe(false);
    expect(isPodFilePath("//example.com/a.png")).toBe(false);
    expect(isPodFilePath("data:image/png;base64,AAAA")).toBe(false);
    expect(isPodFilePath("relative/a.png")).toBe(false);
    expect(isPodFilePath(undefined)).toBe(false);
  });

  it("leaves this app's own routes alone", () => {
    // "starts with a slash" caught these too, so a link to a real page became
    // a file-browser link to a pod file named after the route.
    expect(isPodFilePath("/pod/pod-1/files")).toBe(false);
    expect(isPodFilePath("/settings")).toBe(false);
    expect(isPodFilePath("/logo.png")).toBe(false);
  });

  it("points an image at the authenticated download route", () => {
    // The frontend origin serves no `/me` route, which is why a bare src 404'd.
    const href = podFileDownloadHref("pod-1", "/me/c/d/agent-output/a b.png");
    expect(href).toBe(
      "http://app.127.0.0.1.sslip.io:53664/pods/pod-1/datastore/files/download" +
        "?path=%2Fme%2Fc%2Fd%2Fagent-output%2Fa%20b.png",
    );
  });

  it("uses a download route the backend actually serves", () => {
    // Checked against the published spec, which a gate keeps equal to the
    // backend's routes -- not against a second copy of the same string.
    const spec = JSON.parse(
      readFileSync(new URL("../../../public/openapi.json", import.meta.url), "utf8"),
    ) as { paths: Record<string, Record<string, { parameters?: { name: string; in: string }[] }>> };
    const operation = spec.paths[POD_FILE_DOWNLOAD_ROUTE]?.get;
    expect(operation, `${POD_FILE_DOWNLOAD_ROUTE} is not a GET route`).toBeDefined();
    expect(operation?.parameters?.some((p) => p.name === "path" && p.in === "query")).toBe(true);
  });

  it("points a link at the file browser, folder and file both", () => {
    const href = podFileBrowserHref("pod-1", "/me/c/d/agent-output/a.png");
    const params = new URLSearchParams(href.split("?")[1]);
    expect(href.startsWith("/pod/pod-1/files?")).toBe(true);
    expect(params.get("folder")).toBe("/me/c/d/agent-output");
    expect(params.get("file")).toBe("/me/c/d/agent-output/a.png");
  });

  it("handles a file at the root without inventing a folder", () => {
    const params = new URLSearchParams(podFileBrowserHref("pod-1", "/a.png").split("?")[1]);
    expect(params.get("folder")).toBeNull();
    expect(params.get("file")).toBe("/a.png");
  });
});
