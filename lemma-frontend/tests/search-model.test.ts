import test from "node:test";
import assert from "node:assert/strict";
import { embeddingsStatus, searchModelRow } from "../src/desktop/search-model.ts";

test("each embeddings status reads as a person would say it", () => {
    assert.equal(searchModelRow("ready")?.value, "Ready");
    assert.equal(searchModelRow("preparing")?.value, "Downloading…");
    assert.match(searchModelRow("preparing")!.consequence, /needs internet once/);
    assert.equal(searchModelRow("degraded")?.value, "Couldn’t download — retries automatically");
    assert.equal(searchModelRow("degraded")?.state, "bad");
});

test("no row when search does not embed on this Mac, or the answer is unknown", () => {
    assert.equal(searchModelRow("disabled"), null);
    assert.equal(searchModelRow(null), null);
    assert.equal(searchModelRow("something-new"), null);
});

test("the status is read out of the capabilities body defensively", () => {
    assert.equal(embeddingsStatus({ capabilities: { embeddings: { status: "preparing", detail: "x" } } }), "preparing");
    assert.equal(embeddingsStatus({ capabilities: {} }), null);
    assert.equal(embeddingsStatus(null), null);
    assert.equal(embeddingsStatus({ capabilities: { embeddings: { status: 3 } } }), null);
});
