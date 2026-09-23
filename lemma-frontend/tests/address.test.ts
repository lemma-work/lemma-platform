import test from "node:test";
import assert from "node:assert/strict";
import { NOWHERE, readAddress, tabFromId, writeAddress, type Address } from "../src/shell/address.ts";

/** The grammar has one obligation above all the others: a URL this app wrote
 *  must be a URL this app can read back. Everything below either asserts that
 *  round trip or asserts what happens to a URL the app did not write — which
 *  is the interesting half, because those are the ones that arrive by email.
 */

const POD = "pod_3f9";

function at(tabId: string | null, extra: Partial<Address> = {}): Address {
    return { podId: POD, tabId, conversationId: null, agentName: null, ...extra };
}

/** Every address the grammar can express, and the URL it is spelled as. */
const PLACES: [Address, string][] = [
    [at(null), "/t/" + POD],
    [at("conversation"), "/t/" + POD + "/conversation"],
    [at("conversation", { conversationId: "c_17" }), "/t/" + POD + "/conversation/c_17"],
    [at("library"), "/t/" + POD + "/library"],
    [at("history"), "/t/" + POD + "/history"],
    [at("computer"), "/t/" + POD + "/computer"],
    [at("profile"), "/t/" + POD + "/profile"],
    [at("profile", { agentName: "researcher" }), "/t/" + POD + "/profile/researcher"],
    [at("table:invoices"), "/t/" + POD + "/table/invoices"],
    [at("app:launch-plan"), "/t/" + POD + "/app/launch-plan"],
    [at("record:invoices:7"), "/t/" + POD + "/record/invoices/7"],
    [at("file:/me/notes.md"), "/t/" + POD + "/file/me/notes.md"],
];

test("every place has a URL, and every URL reads back as the place", () => {
    for (const [address, url] of PLACES) {
        assert.equal(writeAddress(address), url, "writing " + (address.tabId ?? "the teammate"));
        assert.deepEqual(readAddress(url), address, "reading " + url);
    }
});

test("the bare root names no teammate", () => {
    assert.deepEqual(readAddress("/t"), NOWHERE);
    assert.deepEqual(readAddress("/t/"), NOWHERE);
    assert.equal(writeAddress(NOWHERE), "/t");
});

test("a file keeps its folders and loses nothing else", () => {
    // The separator between `file` and the first segment is the leading slash
    // a pod path always has, so a deep path survives intact.
    const deep = "file:/me/launch/notes/2026/q1/summary.md";
    assert.equal(writeAddress(at(deep)), "/t/" + POD + "/file/me/launch/notes/2026/q1/summary.md");
    assert.equal(readAddress("/t/" + POD + "/file/me/launch/notes/2026/q1/summary.md").tabId, deep);
});

test("a file name with a character that means something in a URL survives", () => {
    // `#` truncates a URL and a space breaks a copy-paste, so these are encoded
    // per segment. The slashes are structure and stay slashes.
    const tab = "file:/me/Q1 notes #2.md";
    const url = writeAddress(at(tab));
    assert.equal(url, "/t/" + POD + "/file/me/Q1%20notes%20%232.md");
    assert.equal(readAddress(url).tabId, tab);
});

test("a table or app whose name has a space is still one segment", () => {
    // `readableName` turns `launch_plan` into `Launch Plan` for the strip, but
    // the id carries the real name — and a real name can have a space in it.
    const url = writeAddress(at("app:Launch Plan"));
    assert.equal(url, "/t/" + POD + "/app/Launch%20Plan");
    assert.equal(readAddress(url).tabId, "app:Launch Plan");
});

test("a row id may contain a colon", () => {
    // The table name comes off the front rather than the id off the back:
    // taking it off the back would cut a composite key in half.
    const tab = "record:invoices:acme:2026-01";
    const url = writeAddress(at(tab));
    assert.equal(url, "/t/" + POD + "/record/invoices/acme%3A2026-01");
    assert.equal(readAddress(url).tabId, tab);
});

test("a link this build cannot read still opens the teammate", () => {
    // The whole point of falling back rather than refusing. A link from a
    // newer build, or with a typo in it, is somebody trying to arrive
    // somewhere — it lands in the workspace and the shell rewrites the URL.
    for (const url of [
        "/t/" + POD + "/widgets",
        "/t/" + POD + "/table",
        "/t/" + POD + "/table/invoices/extra",
        "/t/" + POD + "/record/invoices",
        "/t/" + POD + "/file",
        "/t/" + POD + "/library/deeper",
        "/t/" + POD + "/conversation/c_1/more",
    ]) {
        assert.deepEqual(readAddress(url), at(null), url + " should land on the teammate");
    }
});

test("a half-copied link is unreadable rather than a guess", () => {
    // A lone `%` makes `decodeURIComponent` throw. One bad segment invalidates
    // the whole address; guessing at the rest would open the wrong teammate.
    assert.deepEqual(readAddress("/t/%/conversation"), NOWHERE);
    assert.deepEqual(readAddress("/t/" + POD + "/file/me/%ZZ.md"), NOWHERE);
});

test("nothing outside the workspace root is an address here", () => {
    assert.deepEqual(readAddress("/"), NOWHERE);
    assert.deepEqual(readAddress("/pod/" + POD + "/data"), NOWHERE);
    assert.deepEqual(readAddress("/connect"), NOWHERE);
});

test("a tab kind this build cannot spell leaves the URL at the teammate", () => {
    // Better a URL that is merely less specific than one that reads back as
    // somewhere else entirely.
    assert.equal(writeAddress(at("widget:abc")), "/t/" + POD);
    assert.equal(writeAddress(at("record:nocolon")), "/t/" + POD);
});

/** The five tabs a link can open before anything has loaded. */

test("a table, a file, a row, the history and the computer rebuild from the URL alone", () => {
    assert.deepEqual(tabFromId("history"), { id: "history", kind: "history", label: "All conversations" });
    assert.deepEqual(tabFromId("computer"), { id: "computer", kind: "computer", label: "Computer" });
    assert.deepEqual(tabFromId("table:invoices"), {
        id: "table:invoices", kind: "table", label: "Invoices", name: "invoices",
    });
    assert.deepEqual(tabFromId("file:/me/notes.md"), {
        id: "file:/me/notes.md", kind: "file", label: "notes.md", path: "/me/notes.md",
    });
    assert.deepEqual(tabFromId("record:invoices:7"), {
        id: "record:invoices:7", kind: "record", label: "Invoices row", table: "invoices", recordId: "7",
    });
});

test("the labels a rebuilt tab carries are the ones the shell would have given it", () => {
    // These have to match `openTable`, `openFile` and `openRecord` exactly, or
    // the same tab opened from a link and from the library is two tabs with
    // two names.
    assert.equal(tabFromId("table:launch_plan")?.label, "Launch Plan");
    assert.equal(tabFromId("file:/a/b/report.pdf")?.label, "report.pdf");
    assert.equal(tabFromId("record:launch_plan:9")?.label, "Launch Plan row");
});

test("an app is not rebuilt from its id", () => {
    // Its frame needs a URL and a status, and those live in the pod's own app
    // list. Inventing a frame pointed at nothing is worse than waiting.
    assert.equal(tabFromId("app:launch-plan"), null);
    assert.equal(tabFromId("conversation"), null);
    assert.equal(tabFromId("library"), null);
    assert.equal(tabFromId("profile"), null);
});

test("a malformed id rebuilds nothing", () => {
    assert.equal(tabFromId("table:"), null);
    assert.equal(tabFromId("file:"), null);
    assert.equal(tabFromId("record:invoices"), null);
    assert.equal(tabFromId("record::7"), null);
    assert.equal(tabFromId("record:invoices:"), null);
});
