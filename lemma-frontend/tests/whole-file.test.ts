import test from "node:test";
import assert from "node:assert/strict";
import { ApiError } from "lemma-sdk";
import { MAX_READ_BYTES } from "../src/computer/machine.ts";
import { wholeFileFrom, type ReadSlice } from "../src/computer/whole-file.ts";

/* A files controller: honours a Range with a 206, caps every answer at the
   server's ceiling, and answers 416 past the end — or, with `ignoresRange`,
   answers every read 200 with the file from byte 0. */
function serving(total: number, { ignoresRange = false } = {}) {
    const asked: Array<{ start: number; end: number }> = [];
    const read: ReadSlice = async range => {
        const whole = { blob: new Blob([new Uint8Array(Math.min(total, MAX_READ_BYTES))]), status: 200 };
        if (!range) return whole;
        asked.push(range);
        if (ignoresRange) return whole;
        if (range.start >= total) throw new ApiError(416, "Requested Range Not Satisfiable");
        const length = Math.min(range.end - range.start + 1, total - range.start, MAX_READ_BYTES);
        return { blob: new Blob([new Uint8Array(length)]), status: 206 };
    };
    return { read, asked };
}

test("a file larger than one read is stitched from ranges", async () => {
    const total = MAX_READ_BYTES * 2 + 99;
    const { read, asked } = serving(total);
    assert.equal((await wholeFileFrom(read)).size, total);
    assert.equal(asked.length, 3);
});

test("a file ending on a chunk boundary ends on the 416", async () => {
    const { read } = serving(MAX_READ_BYTES * 2);
    assert.equal((await wholeFileFrom(read)).size, MAX_READ_BYTES * 2);
});

test("a server that ignores the Range stops the read instead of looping", async () => {
    /* Every ranged read comes back 200 with the first 8 MiB; taking those as
       slices advanced the cursor forever and held a fresh copy each time. */
    const { read, asked } = serving(MAX_READ_BYTES * 3, { ignoresRange: true });
    await assert.rejects(wholeFileFrom(read), /ignored the Range header at byte 0/);
    assert.equal(asked.length, 1);
});

test("a small file from a server that ignores the Range is the whole file", async () => {
    const { read, asked } = serving(1234, { ignoresRange: true });
    assert.equal((await wholeFileFrom(read)).size, 1234);
    assert.equal(asked.length, 1);
});

test("a stale size hint then an ignored Range stops at the next offset", async () => {
    const { read, asked } = serving(MAX_READ_BYTES * 2, { ignoresRange: true });
    await assert.rejects(wholeFileFrom(read, 4096), new RegExp(`at byte ${MAX_READ_BYTES}`));
    assert.equal(asked.length, 1);
});

test("a Content-Range that starts somewhere else is not the slice asked for", async () => {
    const read: ReadSlice = async () => ({
        blob: new Blob([new Uint8Array(MAX_READ_BYTES)]),
        status: 206,
        contentRange: `bytes 0-${MAX_READ_BYTES - 1}/${MAX_READ_BYTES * 4}`,
    });
    await assert.rejects(wholeFileFrom(read), new RegExp(`at byte ${MAX_READ_BYTES}`));
});

test("a real failure is not read as the end of the file", async () => {
    const read: ReadSlice = async () => { throw new ApiError(403, "not yours"); };
    await assert.rejects(wholeFileFrom(read), /not yours/);
});
