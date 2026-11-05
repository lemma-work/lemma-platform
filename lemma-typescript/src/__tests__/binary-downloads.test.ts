// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearTestingToken, setTestingToken } from "../auth.js";
import { LemmaClient } from "../client.js";
import { AppsService } from "../openapi_client/services/AppsService.js";
import { ChannelsService } from "../openapi_client/services/ChannelsService.js";
import { WorkflowsService } from "../openapi_client/services/WorkflowsService.js";

// The generated client once read every non-JSON body with `response.text()`,
// so a channel line's image came back as a mangled string typed `Blob` and the
// timeline threw on `URL.createObjectURL`. The unit tests used a sample API
// that returned a real Blob, which hid it. These go through the real client and
// a real `Response`, only `fetch` stubbed.

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0xff, 0x80, 0x01]);

function bytes(contentType: string): Response {
  return new Response(PNG, { status: 200, headers: { "content-type": contentType } });
}

function signedInClient(): LemmaClient {
  setTestingToken("TESTTOKEN");
  return new LemmaClient({
    apiUrl: "https://api.example.test",
    authUrl: "https://auth.example.test",
    podId: "pod-1",
  });
}

afterEach(() => {
  clearTestingToken();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("binary downloads", () => {
  it("reads a channel line's image as the bytes it was sent", async () => {
    const fetchMock = vi.fn().mockResolvedValue(bytes("image/png"));
    vi.stubGlobal("fetch", fetchMock);

    const blob = await signedInClient().channels.file("pod-1", "channel-1", "line-1", "file-1");

    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("image/png");
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(PNG);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://api.example.test/pods/pod-1/channels/channel-1/messages/line-1/files/file-1",
    );
  });

  it("reads binary from a generated operation as a Blob, not text", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => bytes("application/octet-stream")));
    signedInClient();

    const file = (await ChannelsService.channelMessageFile("pod-1", "c", "l", "f")) as unknown;
    const archive = (await AppsService.appSourceArchiveGet("pod-1", "app")) as unknown;

    expect(file).toBeInstanceOf(Blob);
    expect(new Uint8Array(await (file as Blob).arrayBuffer())).toEqual(PNG);
    expect(archive).toBeInstanceOf(Blob);
  });

  it("still reads a text response as text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<html></html>", { headers: { "content-type": "text/html; charset=utf-8" } }),
      ),
    );
    signedInClient();

    await expect(WorkflowsService.workflowVisualize("pod-1", "flow")).resolves.toBe("<html></html>");
  });
});
