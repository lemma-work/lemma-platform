import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import {
    ROUTE_TEMPLATES,
    isNewView,
    toRouteTemplate,
} from "../src/site/analytics/route-template.ts";
import { readView, writeView, type View } from "../src/outcomes/address.ts";
import { writeAddress, type Address } from "../src/shell/address.ts";

const APP = path.resolve(import.meta.dirname, "../src/app");

/** Every page route on disk, the way Next derives URLs: route groups `(…)`
 *  vanish, private `_…` folders and parallel `@…` slots are not routes. */
function pagesOnDisk(dir = APP, url = ""): string[] {
    const found: string[] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.isDirectory()) {
            const name = entry.name;
            if (name.startsWith("_") || name.startsWith("@")) continue;
            const next = name.startsWith("(") && name.endsWith(")") ? url : url + "/" + name;
            found.push(...pagesOnDisk(path.join(dir, name), next));
        } else if (/^page\.(tsx|ts|jsx|js|mdx)$/.test(entry.name)) {
            found.push(url || "/");
        }
    }
    return found;
}

test("the route list is every page in app/, so a new route cannot leak its segments", () => {
    /* A route missing from the list templates to whatever else matches its
       shape, or to the root catch-all; a route the list keeps after it is gone
       is a template no URL produces. Both drifted once, by 55 routes. */
    assert.deepEqual([...ROUTE_TEMPLATES].sort(), pagesOnDisk().sort());
});

test("a workspace address names the kind of place, never which one", () => {
    const at = (address: Partial<Address>) =>
        toRouteTemplate(writeAddress({ podId: "pod-1", tabId: null, conversationId: null, agentName: null, ...address }));
    assert.equal(toRouteTemplate("/t"), "/t");
    assert.equal(toRouteTemplate("/t/teammates"), "/t/teammates");
    assert.equal(at({}), "/t/[pod]");
    assert.equal(at({ tabId: "conversation" }), "/t/[pod]/conversation");
    assert.equal(at({ tabId: "conversation", conversationId: "c-9" }), "/t/[pod]/conversation/[id]");
    assert.equal(at({ tabId: "space:pages" }), "/t/[pod]/pages");
    assert.equal(at({ tabId: "space:about", section: "people" }), "/t/[pod]/about/people");
    assert.equal(at({ tabId: "table:payroll" }), "/t/[pod]/table/[name]");
    assert.equal(at({ tabId: "app:crm" }), "/t/[pod]/app/[name]");
    assert.equal(at({ tabId: "run:r-1" }), "/t/[pod]/run/[id]");
    assert.equal(at({ tabId: "record:payroll:row-7" }), "/t/[pod]/record/[table]/[id]");
    assert.equal(at({ tabId: "file:/private/q3 layoffs.md" }), "/t/[pod]/file/[...path]");
    assert.equal(at({ tabId: "profile", agentName: "scout" }), "/t/[pod]/profile/[agent]");
    /* Unreadable is not a reason to echo it: a bad escape reads as nowhere. */
    assert.equal(toRouteTemplate("/t/pod-1/%E0%A4%A"), "/t");
});

test("an outcome-frame address names the kind of view, never which one", () => {
    const at = (view: View) => toRouteTemplate(writeView(view));
    assert.equal(at({ name: "today" }), "/o");
    assert.equal(at({ name: "needs" }), "/o/needs-you");
    assert.equal(at({ name: "chats", open: { kind: "chat", podId: "pod-1", id: "new" } }), "/o/chats/[pod]/c/new");
    assert.equal(at({ name: "chats", open: { kind: "chat", podId: "pod-1", id: "c-9" } }), "/o/chats/[pod]/c/[id]");
    assert.equal(at({ name: "chats", open: { kind: "contact", podId: "pod-1", id: "k-1" } }), "/o/chats/[pod]/contacts/[id]");
    assert.equal(at({ name: "outcome", podId: "pod-1", tab: "home" }), "/o/outcome/[pod]");
    assert.equal(at({ name: "outcome", podId: "pod-1", tab: "settings", section: "people" }), "/o/outcome/[pod]/settings/[section]");
    assert.equal(at({ name: "table", podId: "pod-1", table: "payroll" }), "/o/outcome/[pod]/table/[name]");
    assert.equal(at({ name: "file", podId: "pod-1", path: "/private/q3.md" }), "/o/outcome/[pod]/file/[...path]");
    assert.equal(at({ name: "record", podId: "pod-1", table: "payroll", recordId: "r" }), "/o/outcome/[pod]/record/[table]/[id]");
    /* The template is read with the frame's own grammar, so it agrees with it. */
    assert.equal(readView("/o/outcome/pod-1/table/payroll").name, "table");
});

test("other routes still template by the route list, and nothing unknown is echoed", () => {
    assert.equal(toRouteTemplate("/s/contact/ada-lovelace"), "/s/[kind]/[...path]");
    assert.equal(toRouteTemplate("/d/private-share-code"), "/d/[code]");
    assert.equal(toRouteTemplate("/slack/install"), "/slack/install");
    assert.equal(toRouteTemplate("/product/vignettes/7"), "/product/vignettes/[id]");
    assert.equal(toRouteTemplate("/pod/abc/agents/xyz"), "/[...legacy]");
});

test("only a move to a new place is a new pageview", () => {
    assert.equal(isNewView(null, "/t"), true);
    assert.equal(isNewView("/t", "/t"), false);
    /* The workspace settling on where it opened, and a new chat being named. */
    assert.equal(isNewView("/t", "/t/pod-1/home"), false);
    assert.equal(isNewView("/t/pod-1", "/t/pod-1/home"), false);
    assert.equal(isNewView("/t/pod-1/conversation", "/t/pod-1/conversation/c-9"), false);
    assert.equal(isNewView("/o/chats/pod-1/c/new", "/o/chats/pod-1/c/c-9"), false);
    /* Real moves. */
    assert.equal(isNewView("/t/pod-1/home", "/t/pod-1/pages"), true);
    assert.equal(isNewView("/t/pod-1/conversation/c-1", "/t/pod-1/conversation/c-2"), true);
    assert.equal(isNewView("/t/pod-1/home", "/t/pod-2/home"), true);
    assert.equal(isNewView("/o", "/o/needs-you"), true);
    assert.equal(isNewView("/o/chats/pod-1/c/c-1", "/o/chats/pod-1/c/c-2"), true);
    assert.equal(isNewView("/", "/t"), true);
});
