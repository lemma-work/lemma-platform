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
    /* Written under `/l` now, where a word the app also uses is the app's
       place, and the workspace's own words keep their own templates. */
    assert.equal(at({}), "/l/[lemma]");
    assert.equal(at({ tabId: "conversation" }), "/l/[pod]/conversation");
    assert.equal(at({ tabId: "conversation", conversationId: "c-9" }), "/l/[pod]/conversation/[id]");
    assert.equal(at({ tabId: "space:pages" }), "/l/[pod]/pages");
    assert.equal(at({ tabId: "space:about", section: "people" }), "/l/[pod]/about/people");
    assert.equal(at({ tabId: "table:payroll" }), "/l/[pod]/table/[name]");
    assert.equal(at({ tabId: "app:crm" }), "/l/[pod]/app/[name]");
    assert.equal(at({ tabId: "run:r-1" }), "/l/[pod]/run/[id]");
    assert.equal(at({ tabId: "record:payroll:row-7" }), "/l/[pod]/record/[table]/[id]");
    assert.equal(at({ tabId: "file:/private/q3 layoffs.md" }), "/l/[pod]/file/[...path]");
    assert.equal(at({ tabId: "profile", agentName: "scout" }), "/l/[pod]/profile/[agent]");
    /* Older links still say `/t`, and are read the same way. */
    assert.equal(toRouteTemplate("/t"), "/home");
    assert.equal(toRouteTemplate("/t/teammates"), "/t/teammates");
    assert.equal(toRouteTemplate("/t/pod-1/table/payroll"), "/t/[pod]/table/[name]");
    /* Unreadable is not a reason to echo it: a bad escape reads as nowhere. */
    assert.equal(toRouteTemplate("/t/pod-1/%E0%A4%A"), "/t");
    assert.equal(toRouteTemplate("/l"), "/l");
    assert.equal(toRouteTemplate("/l/teammates"), "/l");
    assert.equal(toRouteTemplate("/l/pod-1/file/private/q3.md"), "/l/[pod]/file/[...path]");
});

test("an app address names the kind of view, never which one", () => {
    const at = (view: View) => toRouteTemplate(writeView(view));
    assert.equal(at({ name: "today" }), "/home");
    assert.equal(at({ name: "needs" }), "/needs");
    assert.equal(at({ name: "apps" }), "/apps");
    assert.equal(at({ name: "connectors" }), "/connectors");
    assert.equal(at({ name: "outcomes" }), "/l");
    assert.equal(at({ name: "hire" }), "/l/new");
    assert.equal(at({ name: "chats", open: { kind: "chat", podId: "pod-1", id: "new" } }), "/l/[lemma]/chats/new");
    assert.equal(at({ name: "chats", open: { kind: "chat", podId: "pod-1", id: "c-9" } }), "/l/[lemma]/chats/[id]");
    assert.equal(at({ name: "chats", open: { kind: "shared", podId: "pod-1", id: "g-1" } }), "/l/[lemma]/groups/[id]");
    assert.equal(at({ name: "chats", open: { kind: "contact", podId: "pod-1", id: "k-1" } }), "/l/[lemma]/contacts/[id]");
    assert.equal(at({ name: "outcome", podId: "pod-1", tab: "home" }), "/l/[lemma]");
    assert.equal(at({ name: "outcome", podId: "pod-1", tab: "made" }), "/l/[lemma]/files");
    assert.equal(at({ name: "outcome", podId: "pod-1", tab: "settings", section: "people" }), "/l/[lemma]/settings/[section]");
    assert.equal(at({ name: "outcome", podId: "pod-1", tab: "automations", section: "schedules" }), "/l/[lemma]/automations/schedules", "one of a fixed few views");
    assert.equal(at({ name: "outcome", podId: "pod-1", tab: "chats", section: "channels" }), "/l/[lemma]/groups");
    assert.equal(at({ name: "table", podId: "pod-1", table: "payroll" }), "/l/[lemma]/tables/[name]");
    assert.equal(at({ name: "file", podId: "pod-1", path: "/private/q3.md" }), "/l/[lemma]/files/[...path]");
    assert.equal(at({ name: "record", podId: "pod-1", table: "payroll", recordId: "r" }), "/l/[lemma]/tables/[table]/[id]");
    assert.equal(at({ name: "computer", podId: "pod-1", conversationId: "c-9" }), "/l/[lemma]/computer/[id]");
    /* The template is read with the app's own grammar, so it agrees with it. */
    assert.equal(readView("/l/pod-1/tables/payroll")?.name, "table");
    /* The frame's first addresses, under `/t` and `/o`, template as the place
       they name; a workspace address under them as the workspace's. */
    assert.equal(toRouteTemplate("/t/outcome/pod-1/table/payroll"), "/l/[lemma]/tables/[name]");
    assert.equal(toRouteTemplate("/o/outcome/pod-1/table/payroll"), "/l/[lemma]/tables/[name]");
    assert.equal(toRouteTemplate("/o/pod-1/table/payroll"), "/t/[pod]/table/[name]");
    /* A word every object answers to is as unknown as any other word, not a
       template of `undefined`. */
    for (const word of ["/constructor", "/__proto__", "/toString"]) {
        assert.equal(toRouteTemplate(word), toRouteTemplate("/not-a-place"), word);
    }
});

test("other routes still template by the route list, and nothing unknown is echoed", () => {
    assert.equal(toRouteTemplate("/s/contact/ada-lovelace"), "/s/[kind]/[...path]");
    assert.equal(toRouteTemplate("/d/private-share-code"), "/d/[code]");
    assert.equal(toRouteTemplate("/slack/install"), "/slack/install");
    assert.equal(toRouteTemplate("/product/vignettes/7"), "/product/vignettes/[id]");
    assert.equal(toRouteTemplate("/pod/abc/agents/xyz"), "/[...legacy]");
});

test("only a move to a new place is a new pageview", () => {
    assert.equal(isNewView(null, "/home"), true);
    assert.equal(isNewView("/home", "/home"), false);
    /* An old link written again as the app's own address for the same
       place, and a new chat being named. */
    assert.equal(isNewView("/l/pod-1/conversation/c-9", "/l/pod-1/chats/c-9"), false);
    assert.equal(isNewView("/l/pod-1/about/connections", "/l/pod-1/settings/connections"), false);
    assert.equal(isNewView("/t/outcome/pod-1/table/payroll", "/l/pod-1/tables/payroll"), false);
    assert.equal(isNewView("/l/pod-1/chats/new", "/l/pod-1/chats/c-9"), false);
    /* The workspace's own addresses, between themselves. */
    assert.equal(isNewView("/l/pod-1/conversation", "/l/pod-1/conversation/c-9"), false);
    assert.equal(isNewView("/l/pod-1/home", "/l/pod-1/pages"), true);
    assert.equal(isNewView("/l/pod-1/conversation/c-1", "/l/pod-1/conversation/c-2"), true);
    /* Real moves. */
    assert.equal(isNewView("/home", "/needs"), true);
    assert.equal(isNewView("/l/pod-1", "/l/pod-2"), true);
    assert.equal(isNewView("/l/pod-1/chats/c-1", "/l/pod-1/chats/c-2"), true);
    assert.equal(isNewView("/l/pod-1", "/l/pod-1/table/payroll"), true, "a workspace link followed from inside the app");
    assert.equal(isNewView("/", "/home"), true);
});

