import test from "node:test";
import assert from "node:assert/strict";
import { podAccess } from "../src/shell/pod-access.ts";
import type { Pod } from "../src/data/types.ts";

const pod: Pod = { id: "linked", orgId: "other-org", name: "Teammate", iconUrl: null, teammate: { name: "Teammate", initials: "T" }, subtitle: "", members: [], waiting: "" };

test("a linked pod omitted from a stale or different organization's list still opens", () => {
    assert.deepEqual(podAccess(pod.id, [], { status: "success", isFetching: false, data: pod }), { state: "ready", pod });
});

test("a list miss waits for verification instead of claiming access is denied", () => {
    assert.deepEqual(podAccess(pod.id, [], { status: "pending", isFetching: true }), { state: "loading", pod: null });
});

test("a named link never falls back to a different listed pod", () => {
    assert.equal(podAccess("unknown", [pod], { status: "pending", isFetching: true }).pod, null);
});

test("only an explicit forbidden response shows the join screen", () => {
    for (const [statusCode, expected] of [[403, "denied"], [404, "missing"], [500, "error"], [401, "loading"]] as const) {
        assert.equal(podAccess(pod.id, [], { status: "error", error: { statusCode }, isFetching: false }).state, expected);
    }
    assert.equal(podAccess(pod.id, [], { status: "error", error: new TypeError("Offline"), isFetching: false }).state, "error");
});

test("retrying a cached denial shows verification while the new response is pending", () => {
    assert.equal(podAccess(pod.id, [], { status: "error", error: { statusCode: 403 }, isFetching: true }).state, "loading");
});
