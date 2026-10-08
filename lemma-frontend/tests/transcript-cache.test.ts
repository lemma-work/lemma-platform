import test from "node:test";
import assert from "node:assert/strict";
import { PREFETCH_FRESH_MS, TranscriptCache } from "../src/thread/transcript-cache.ts";

/** The conversations kept so going back to one is instant. */

type Entry = { conversation: { id: string } | null; status?: string; messages: string[]; olderToken: string | null };

function entry(id: string, messages: string[] = ["hi"]): Entry {
    return { conversation: { id }, status: "IDLE", messages, olderToken: null };
}

test("a conversation left is there to go back to, per space", () => {
    const cache = new TranscriptCache<string, { id: string }>();
    cache.save("pod-a", "c1", entry("c1", ["one", "two"]));

    assert.deepEqual(cache.get("pod-a", "c1")?.messages, ["one", "two"]);
    assert.equal(cache.get("pod-b", "c1"), null, "another space's conversation is not this one");
    assert.equal(cache.get("pod-a", null), null);
});

test("only the last few are kept, the oldest going first", () => {
    const cache = new TranscriptCache<string, { id: string }>(2);
    cache.save("pod", "c1", entry("c1"));
    cache.save("pod", "c2", entry("c2"));
    cache.save("pod", "c1", entry("c1", ["newer"]));
    cache.save("pod", "c3", entry("c3"));

    assert.equal(cache.get("pod", "c2"), null);
    assert.deepEqual(cache.get("pod", "c1")?.messages, ["newer"]);
    assert.ok(cache.get("pod", "c3"));
});

test("resting on a conversation twice fetches it once", async () => {
    let now = 1_000;
    const cache = new TranscriptCache<string, { id: string }>(8, () => now);
    let fetched = 0;
    const fetch = async () => { fetched += 1; return entry("c1"); };

    await Promise.all([cache.prefetch("pod", "c1", fetch), cache.prefetch("pod", "c1", fetch)]);
    await cache.prefetch("pod", "c1", fetch);
    assert.equal(fetched, 1);

    now += PREFETCH_FRESH_MS + 1;
    await cache.prefetch("pod", "c1", fetch);
    assert.equal(fetched, 2, "a stale copy is fetched again");
});

test("a prefetch never overwrites what the open pane saved while it was in the air", async () => {
    let now = 1_000;
    const cache = new TranscriptCache<string, { id: string }>(8, () => now);
    let land: (value: Entry) => void = () => {};
    const going = cache.prefetch("pod", "c1", () => new Promise<Entry>((resolve) => { land = resolve; }));

    now += 10;
    cache.save("pod", "c1", entry("c1", ["from the pane"]));
    land(entry("c1", ["from the prefetch"]));
    await going;

    assert.deepEqual(cache.get("pod", "c1")?.messages, ["from the pane"]);
});

test("a failed prefetch is forgotten, not cached", async () => {
    const cache = new TranscriptCache<string, { id: string }>();
    await cache.prefetch("pod", "c1", async () => { throw new Error("offline"); });

    assert.equal(cache.get("pod", "c1"), null);
});
