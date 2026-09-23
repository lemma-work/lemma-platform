import { strict as assert } from "node:assert";
import test from "node:test";
import { TranscriptLog, chunkForAppend, requestFrom } from "@/call/live-turns";

/** GPT-Live's delegation event carries no task text, so what the person asked
 *  for is whatever this module can reconstruct from the transcript. These are
 *  the cases where getting it wrong is the difference between the teammate
 *  answering the question and answering the previous one. */

test("fragments from one speaker join when they touch and split when they do not", () => {
    const log = new TranscriptLog();
    log.append({ speaker: "them", delta: "what is", startMs: 0, endMs: 200 });
    log.append({ speaker: "them", delta: "the deploy status", startMs: 250, endMs: 900 });
    log.append({ speaker: "them", delta: "actually never mind", startMs: 4000, endMs: 4600 });

    const cut = log.take(5000);
    assert.equal(cut.said, "what is the deploy status actually never mind");
    assert.equal(cut.heard, "");
});

test("the cut advances, so a second delegation does not re-ask the first one", () => {
    const log = new TranscriptLog();
    log.append({ speaker: "them", delta: "check the build", startMs: 0, endMs: 800 });
    assert.equal(log.take(1000).said, "check the build");

    log.append({ speaker: "them", delta: "and the tests", startMs: 2000, endMs: 2600 });
    const second = log.take(3000);
    assert.equal(second.said, "and the tests");
});

test("a fragment that lands late for speech already taken is not asked twice", () => {
    const log = new TranscriptLog();
    log.append({ speaker: "them", delta: "ship it", startMs: 0, endMs: 700 });
    assert.equal(log.take(1000).said, "ship it");

    /* Delivery is uneven: this is a fragment for speech that ended before the
       cut, arriving after it. Taking by the clock rather than by queue order
       is what makes it a no-op instead of a duplicate request. */
    log.append({ speaker: "them", delta: "ship it", startMs: 100, endMs: 700 });
    assert.equal(log.take(2000).said, "");
});

test("speech past the delegation's window waits for the next cut", () => {
    const log = new TranscriptLog();
    log.append({ speaker: "them", delta: "open the file", startMs: 0, endMs: 900 });
    log.append({ speaker: "them", delta: "and the one next to it", startMs: 3000, endMs: 3800 });

    assert.equal(log.take(1000).said, "open the file");
    assert.equal(log.take(4000).said, "and the one next to it");
});

test("what the voice said in the window comes back as context, not as the request", () => {
    const log = new TranscriptLog();
    log.append({ speaker: "us", delta: "the staging one or production?", startMs: 0, endMs: 900 });
    log.append({ speaker: "them", delta: "production", startMs: 1000, endMs: 1400 });

    const cut = log.take(2000);
    assert.equal(cut.said, "production");
    assert.equal(cut.heard, "the staging one or production?");

    /* "production" on its own is not a request anyone can act on. */
    const request = requestFrom(cut);
    assert.match(request, /the staging one or production\?/);
    assert.ok(request.endsWith("production"));
});

test("a cut with nothing said is not a request", () => {
    assert.equal(requestFrom({ said: "", heard: "still here" }), "");
    assert.equal(requestFrom({ said: "restart it", heard: "" }), "restart it");
});

test("latest reaches past the cut, which is what makes it a fallback", () => {
    const log = new TranscriptLog();
    log.append({ speaker: "them", delta: "roll it back", startMs: 0, endMs: 800 });
    log.take(1000);
    assert.equal(log.latest("them"), "roll it back");
    assert.equal(log.latest("us"), "");
});

test("appends stay inside the budget and break on sentences", () => {
    assert.deepEqual(chunkForAppend(""), []);
    assert.deepEqual(chunkForAppend("short"), ["short"]);

    const sentences = "One sentence here. Two sentence here. Three sentence here.";
    const chunks = chunkForAppend(sentences, 30);
    assert.ok(chunks.every((chunk) => chunk.length <= 30), chunks.join(" | "));
    assert.equal(chunks.join(" "), sentences);
    assert.ok(chunks.every((chunk) => /[.!?]$/.test(chunk)));
});

test("text with no boundary to break on is cut by length rather than dropped", () => {
    const run = "x".repeat(250);
    const chunks = chunkForAppend(run, 100);
    assert.deepEqual(chunks.map((chunk) => chunk.length), [100, 100, 50]);
    assert.equal(chunks.join(""), run);
});
