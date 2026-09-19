import { describe, expect, it } from "vitest";

import { ApiError } from "../http.js";
import type { HttpClient } from "../http.js";
import { MAX_READ_BYTES, WorkspaceNamespace } from "../namespaces/workspace.js";

/**
 * A server that behaves like the files controller: it honours a `Range`, it
 * never returns more than `MAX_READ_BYTES` in one response however much is
 * asked for, and it answers 416 for an offset past the end.
 */
function serving(totalBytes: number) {
  const asked: Array<{ start: number; end: number }> = [];
  const http = {
    async requestBytes(
      _method: string,
      _path: string,
      options: { headers?: Record<string, string> } = {},
    ): Promise<Blob> {
      const header = options.headers?.Range;
      if (!header) {
        // No range: the whole file, still capped.
        return new Blob([new Uint8Array(Math.min(totalBytes, MAX_READ_BYTES))]);
      }
      const [first, last] = header.slice("bytes=".length).split("-");
      const start = Number(first);
      const end = Number(last);
      asked.push({ start, end });
      if (start >= totalBytes) {
        throw new ApiError(416, "Requested Range Not Satisfiable");
      }
      const length = Math.min(end - start + 1, totalBytes - start, MAX_READ_BYTES);
      return new Blob([new Uint8Array(length)]);
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

  it("does not swallow a real failure as an end of file", async () => {
    const http = {
      async requestBytes(): Promise<Blob> {
        throw new ApiError(403, "not yours");
      },
    } as unknown as HttpClient;

    await expect(new WorkspaceNamespace(http).readWholeFile("/home/user/x")).rejects.toThrow(
      "not yours",
    );
  });
});
