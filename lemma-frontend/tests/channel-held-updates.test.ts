import test from "node:test";
import assert from "node:assert/strict";
import { QueryClient } from "@tanstack/react-query";
import { HeldUpdates } from "../src/channels/held-updates.ts";

/** A line heard on the socket while the timeline's first read is still out
 *  is not lost: it waits for the read, and lands on it. */

const append = (id: string) => (was: string[]) => (was.includes(id) ? was : [...was, id]);

test("a change heard while the first read is out is applied once the read lands", async () => {
    const cache = new QueryClient();
    const held = new HeldUpdates();
    const stop = held.watch(cache);
    let answer!: (lines: string[]) => void;
    const reading = cache.fetchQuery({ queryKey: ["lines"], queryFn: () => new Promise<string[]>((resolve) => { answer = resolve; }) });
    held.apply(cache, ["lines"], append("l9"));
    held.apply(cache, ["lines"], append("l10"));
    assert.equal(held.size, 2, "held, not dropped");
    /* The read began before l9 was written, so it does not have it. */
    answer(["l7", "l8"]);
    await reading;
    assert.deepEqual(cache.getQueryData(["lines"]), ["l7", "l8", "l9", "l10"]);
    assert.equal(held.size, 0);
    /* Once read, a change applies at once; a repeat changes nothing. */
    held.apply(cache, ["lines"], append("l10"));
    held.apply(cache, ["lines"], append("l11"));
    assert.deepEqual(cache.getQueryData(["lines"]), ["l7", "l8", "l9", "l10", "l11"]);
    stop();
    cache.clear();
});

test("a query nobody asked for is not held for: whoever asks reads it fresh", () => {
    const cache = new QueryClient();
    const held = new HeldUpdates();
    held.apply(cache, ["thread", "root-1"], append("r1"));
    assert.equal(held.size, 0);
    assert.equal(cache.getQueryData(["thread", "root-1"]), undefined);
});
