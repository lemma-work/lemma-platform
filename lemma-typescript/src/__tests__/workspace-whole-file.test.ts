import { describe, expect, it } from "vitest";

import { ApiError } from "../http.js";
import type { HttpClient } from "../http.js";
import { MAX_READ_BYTES, WorkspaceNamespace } from "../namespaces/workspace.js";

/**
 * A server that behaves like the files controller: it honours a `Range`, it
 * never returns more than `MAX_READ_BYTES` in one response however much is
 * asked for, and it answers 416 for an offset past the end.
 */
function serving(totalBytes: number, { ignoresRange = false } = {}) {
  const asked: Array<{ start: number; end: number }> = [];
  const respond = async (
    _method: string,
    _path: string,
    options: { headers?: Record<string, string> } = {},
  ): Promise<{ blob: Blob; status: number; contentRange: string | null }> => {
    const header = options.headers?.Range;
    const whole = () => ({
      // No range, or one ignored: the whole file from byte 0, still capped.
      blob: new Blob([new Uint8Array(Math.min(totalBytes, MAX_READ_BYTES))]),
      status: 200,
      contentRange: null,
    });
    if (!header) return whole();
    const [first, last] = header.slice("bytes=".length).split("-");
    const start = Number(first);
    const end = Number(last);
    asked.push({ start, end });
    if (ignoresRange) return whole();
    if (start >= totalBytes) {
      throw new ApiError(416, "Requested Range Not Satisfiable");
    }
    const length = Math.min(end - start + 1, totalBytes - start, MAX_READ_BYTES);
    return {
      blob: new Blob([new Uint8Array(length)]),
      status: 206,
      contentRange: `bytes ${start}-${start + length - 1}/${totalBytes}`,
    };
  };
  const http = {
    requestBytesResponse: respond,
    async requestBytes(...args: Parameters<typeof respond>): Promise<Blob> {
      return (await respond(...args)).blob;
    },
  } as unknown as HttpClient;
  return { workspace: new WorkspaceNamespace(http), asked };
}

describe("readWholeFile", () => {
  it("reads a file whose size the caller does not know", async () => {
    // The explorer's reload case: the open path comes back from the URL and
    // the size does not, so it passed 0 and every download after a reload
    // truncated at 8 MiB under the whole file's name.
    const total = MAX_READ_BYTES * 2 + 1234;
    const { workspace } = serving(total);

    const blob = await workspace.readWholeFile("/home/user/big.bin");

    expect(blob.size).toBe(total);
  });

  it("does not skip bytes when asked for a chunk larger than the server allows", async () => {
    // The server caps at 8 MiB whatever is asked for. Advancing the cursor
    // by the requested 64 MiB rather than the 8 that arrived dropped seven
    // eighths of the file and produced a plausible-looking short download.
    const total = MAX_READ_BYTES * 3;
    const { workspace, asked } = serving(total);

    const blob = await workspace.readWholeFile("/home/user/big.bin", total, 64 * 1024 * 1024);

    expect(blob.size).toBe(total);
    expect(asked.every(({ start, end }) => end - start + 1 <= MAX_READ_BYTES)).toBe(true);
  });

  it("stops at a file that ends exactly on a chunk boundary", async () => {
    // There is no short read to end on, so the loop runs one request past
    // the file and must read the 416 as "done" rather than as a failure.
    const total = MAX_READ_BYTES * 2;
    const { workspace } = serving(total);

    const blob = await workspace.readWholeFile("/home/user/exact.bin");

    expect(blob.size).toBe(total);
  });

  it("reads an empty file as empty rather than failing", async () => {
    const { workspace } = serving(0);

    expect((await workspace.readWholeFile("/home/user/empty.txt")).size).toBe(0);
  });

  it("still reads a known-small file in one unranged request", async () => {
    const { workspace, asked } = serving(1024);

    const blob = await workspace.readWholeFile("/home/user/small.txt", 1024);

    expect(blob.size).toBe(1024);
    expect(asked).toEqual([]);
  });

  it("does not truncate when the size it was given is stale", async () => {
    // The hint lets a small file skip straight to one unranged read. A file
    // that grew since the listing that measured it would then come back
    // capped at the server's ceiling, under the whole file's name -- the
    // exact failure the ranged path exists to prevent, reached through the
    // shortcut past it.
    const total = MAX_READ_BYTES * 2 + 77;
    const { workspace } = serving(total);

    const blob = await workspace.readWholeFile("/home/user/grew.bin", 4096);

    expect(blob.size).toBe(total);
  });

  it("stops, rather than looping, when the server ignores the Range on a large file", async () => {
    // Every ranged read comes back 200 with the first 8 MiB. Taking those as
    // slices advanced the cursor forever and held a fresh copy each time.
    const { workspace, asked } = serving(MAX_READ_BYTES * 3, { ignoresRange: true });

    await expect(workspace.readWholeFile("/home/user/big.bin")).rejects.toThrow(
      /ignored the Range header/,
    );
    expect(asked.length).toBe(1);
  });

  it("takes a whole small file from a server that ignores the Range", async () => {
    const { workspace, asked } = serving(1234, { ignoresRange: true });

    const blob = await workspace.readWholeFile("/home/user/small.txt");

    expect(blob.size).toBe(1234);
    expect(asked.length).toBe(1);
  });

  it("stops when a stale size hint is followed by a server that ignores the Range", async () => {
    // The hint's unranged read comes back at the ceiling, so the loop goes on
    // from 8 MiB -- and must not accept the file-from-byte-0 it is sent.
    const { workspace, asked } = serving(MAX_READ_BYTES * 2, { ignoresRange: true });

    await expect(workspace.readWholeFile("/home/user/grew.bin", 4096)).rejects.toThrow(
      /at byte 8388608/,
    );
    expect(asked.length).toBe(1);
  });

  it("rejects a slice whose Content-Range starts somewhere else", async () => {
    const http = {
      async requestBytesResponse() {
        return {
          blob: new Blob([new Uint8Array(MAX_READ_BYTES)]),
          status: 206,
          contentRange: `bytes 0-${MAX_READ_BYTES - 1}/${MAX_READ_BYTES * 4}`,
        };
      },
    } as unknown as HttpClient;
    const workspace = new WorkspaceNamespace(http);

    // The first slice starts at 0 and is fine; the second claims 0 again.
    await expect(workspace.readWholeFile("/home/user/big.bin")).rejects.toThrow(
      /ignored the Range header/,
    );
  });

  it("does not swallow a real failure as an end of file", async () => {
    const http = {
      async requestBytesResponse(): Promise<never> {
        throw new ApiError(403, "not yours");
      },
    } as unknown as HttpClient;

    await expect(new WorkspaceNamespace(http).readWholeFile("/home/user/x")).rejects.toThrow(
      "not yours",
    );
  });
});
