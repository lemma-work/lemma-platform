import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/sdk/lemma-client", () => ({
  getLemmaApiBaseUrl: () => "http://app.127.0.0.1.sslip.io:53664/",
}));

import {
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

  it("points an image at the authenticated download route", () => {
    // The frontend origin serves no `/me` route, which is why a bare src 404'd.
    const href = podFileDownloadHref("pod-1", "/me/c/d/agent-output/a b.png");
    expect(href).toBe(
      "http://app.127.0.0.1.sslip.io:53664/pods/pod-1/files/download" +
        "?path=%2Fme%2Fc%2Fd%2Fagent-output%2Fa%20b.png",
    );
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
