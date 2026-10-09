import { test } from "node:test";
import assert from "node:assert/strict";

import { parseDisplayResource, resourceHref, resourceLabel } from "../src/thread/display-resource.ts";
import { readAddress } from "../src/shell/address.ts";

const SITE = "https://lemma.example";
const POD = "0d6b7c62-4a51-4c7e-9a52-5f1d1a2b3c4d";

function href(args: Record<string, unknown>, result?: unknown): string | null {
    const resource = parseDisplayResource(args, result);
    assert.ok(resource, "parses");
    return resourceHref(SITE, POD, resource);
}

test("every in-app resource link is a route this app reads", () => {
    const cases: Array<[Record<string, unknown>, string]> = [
        [{ type: "FILE", path: "/me/reports/q3 review #2.pdf" }, "file:/me/reports/q3 review #2.pdf"],
        [{ type: "TABLE", name: "deals" }, "table:deals"],
        [{ type: "WORKFLOW", name: "intake" }, "workflow:intake"],
        [{ type: "AGENT", name: "scout" }, "profile"],
        [{ type: "SCHEDULE" }, "space:about"],
    ];
    for (const [args, tab] of cases) {
        const url = href(args);
        assert.ok(url?.startsWith(SITE + "/t/" + POD), url ?? "no link");
        const address = readAddress(new URL(url!).pathname);
        assert.equal(address.podId, POD);
        assert.equal(address.tabId, tab, url!);
    }
});

test("a live browser links to the address its result carries, and only that", () => {
    assert.equal(href({ type: "BROWSER" }, { success: true, url: "https://browser.example/live" }), "https://browser.example/live");
    assert.equal(href({ type: "BROWSER" }), null, "no result yet, nothing to open");
    const resource = parseDisplayResource({ type: "BROWSER" });
    assert.equal(resource && resourceLabel(resource), "Live browser");
});

test("a live browser whose address has expired stops offering it", async () => {
    const { liveEnded } = await import("../src/thread/display-resource.ts");
    const now = Date.parse("2026-10-09T12:00:00Z");
    const result = (until: string) => ({ success: true, url: "https://browser.example/live", expires_at: until });
    const live = parseDisplayResource({ type: "BROWSER" }, result("2026-10-09T12:30:00Z"))!;
    const ended = parseDisplayResource({ type: "BROWSER" }, result("2026-10-09T11:30:00Z"))!;
    assert.equal(resourceHref(SITE, POD, live, now), "https://browser.example/live");
    assert.equal(resourceHref(SITE, POD, ended, now), null, "an old card is not a dead link");
    assert.equal(liveEnded(ended, now), true);
    assert.equal(liveEnded(parseDisplayResource({ type: "BROWSER" }, { url: "https://b.example" })!, now), false);
});
