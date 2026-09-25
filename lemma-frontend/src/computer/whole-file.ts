import { ApiError } from "lemma-sdk";
import { MAX_READ_BYTES } from "./machine";

/** One read of the file: its bytes and how the server answered.
 *
 *  `status` because a ranged read is only a slice if it came back 206. A
 *  server that ignored the `Range` answers 200 with the file from byte 0, and
 *  the bytes alone cannot tell the two apart. `contentRange` is read when the
 *  server exposes it; cross-origin it usually is not, and the status is what
 *  is left. */
export interface Slice {
    blob: Blob;
    status: number;
    contentRange?: string | null;
}

export type ReadSlice = (range?: { start: number; end: number }) => Promise<Slice>;

/** Whether an answer to `Range: bytes=<start>-…` is the slice asked for: a
 *  206, or a `Content-Range` that starts at `start`. One that starts anywhere
 *  else is a different slice, whatever the status. */
export function rangeHonoured(slice: Slice, start: number): boolean {
    if (slice.contentRange) {
        const match = /^bytes (\d+)-\d+\/(?:\d+|\*)$/.exec(slice.contentRange.trim());
        return match !== null && Number(match[1]) === start;
    }
    return slice.status === 206;
}

/** A whole file, however big, read a range at a time through `read`.
 *
 *  The size is discovered rather than trusted: this reads until the server
 *  returns a short slice or a 416, so a `sizeBytes` of 0 is correct and only
 *  lets a small file skip straight to a single unranged read. */
export async function wholeFileFrom(read: ReadSlice, sizeBytes = 0): Promise<Blob> {
    const parts: Blob[] = [];
    let start = 0;
    if (sizeBytes > 0 && sizeBytes <= MAX_READ_BYTES) {
        const only = (await read()).blob;
        /* Short of the ceiling means that was the whole file. Exactly the
           ceiling means the hint was stale — the file grew after the listing
           that measured it — and returning here would truncate, which is the
           failure this function exists to prevent. */
        if (only.size < MAX_READ_BYTES) return only;
        parts.push(only);
        start = only.size;
    }
    for (;;) {
        let slice: Slice;
        try {
            slice = await read({ start, end: start + MAX_READ_BYTES - 1 });
        } catch (error) {
            /* 416. The file ended exactly on a chunk boundary, or it is empty:
               both are "there is nothing at this offset", and neither is a
               failure. Any other status is. */
            if (error instanceof ApiError && error.statusCode === 416) break;
            throw error;
        }
        const part = slice.blob;
        if (!rangeHonoured(slice, start)) {
            /* The server ignored the Range and sent the file from byte 0. Ask
               at the next offset and it does the same, so this never ends and
               holds another copy each time round. From the start, short of the
               ceiling, that is simply the whole file; anywhere else it cannot
               be stitched into one. */
            if (start === 0 && part.size < MAX_READ_BYTES) return part;
            throw new Error(
                `The server ignored the Range header at byte ${start}; ` +
                "stopping rather than stitching repeated copies of the file.",
            );
        }
        if (part.size === 0) break;
        parts.push(part);
        start += part.size;
        /* Short of what was asked for means the server ran out of file, which
           is the ordinary way this ends — one request more than the file
           needs, rather than a 416 every time. */
        if (part.size < MAX_READ_BYTES) break;
    }
    return new Blob(parts);
}
