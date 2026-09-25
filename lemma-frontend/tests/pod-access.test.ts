import test from "node:test";
import assert from "node:assert/strict";
import { podAccess, readLastPods, rememberPod } from "../src/shell/pod-access.ts";
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

const other: Pod = { ...pod, id: "other", orgId: "org", name: "Other" };
const last: Pod = { ...pod, id: "last", orgId: "org", name: "Last" };

test("the bare workspace opens the teammate last opened in this organization", () => {
    const lookup = { status: "pending", isFetching: false } as const;
    assert.equal(podAccess(null, [other, last], lookup, "last").pod, last);
});

test("a remembered teammate that is gone falls back to the first one listed", () => {
    const lookup = { status: "pending", isFetching: false } as const;
    assert.equal(podAccess(null, [other, last], lookup, "deleted").pod, other);
    assert.equal(podAccess(null, [other, last], lookup, null).pod, other);
});

test("a link always wins over the remembered teammate", () => {
    assert.equal(podAccess(other.id, [other, last], { status: "success", isFetching: false, data: other }, "last").pod, other);
});

test("the last teammate is remembered per organization", () => {
    const first = rememberPod({}, "org-a", "pod-1");
    const second = rememberPod(first, "org-b", "pod-2");
    assert.deepEqual(second, { "org-a": "pod-1", "org-b": "pod-2" });
    assert.deepEqual(rememberPod(second, "org-a", "pod-3"), { "org-a": "pod-3", "org-b": "pod-2" });
    /* Unchanged is the same object, so a re-render does not rewrite storage. */
    assert.equal(rememberPod(second, "org-a", "pod-1"), second);
    assert.equal(rememberPod(second, null, "pod-9"), second);
});

test("whatever is in storage reads back as a map of ids, or nothing", () => {
    assert.deepEqual(readLastPods({ "org-a": "pod-1", "org-b": 7 }), { "org-a": "pod-1" });
    for (const junk of [null, "pod-1", ["pod-1"], 3]) assert.deepEqual(readLastPods(junk), {});
});
