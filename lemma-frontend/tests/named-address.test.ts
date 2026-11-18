import test from "node:test";
import assert from "node:assert/strict";
import { looksNamed, named, namedTail, slug, unnamed, type Names } from "../src/outcomes/named-address.ts";
import { readView, writeView, type View } from "../src/outcomes/address.ts";
import { toRouteTemplate } from "../src/site/analytics/route-template.ts";

/** An address in names is a second spelling of a place the app's own grammar
 *  already names by id. Its one obligation is the same as that grammar's: what
 *  it writes, it reads back to the same place -- and what it cannot spell
 *  without losing that, it leaves as an id. */

const SALES = "0f3a9c1e-7b52-4c1d-9a3e-5d2f8e6b1a47";
const SUPPORT = "8b1d6e0a-2c4f-4a7b-b1c3-9e5f7a2d4c6b";

const ACME: Names = {
    org: "Acme Inc.",
    lemmas: [
        { id: SALES, name: "Sales" },
        { id: SUPPORT, name: "Customer Support" },
        /* Reads as one of a Lemma's own sections, so it cannot follow the
           organization's name and still mean itself. */
        { id: "p-chats", name: "Chats" },
        /* Two that read the same. */
        { id: "p-ops-1", name: "Ops" },
        { id: "p-ops-2", name: "ops!" },
        /* Nothing to spell. */
        { id: "p-emoji", name: "🚀" },
    ],
};

test("a name is one word of an address", () => {
    assert.equal(slug("Sales"), "sales");
    assert.equal(slug("Customer Support"), "customer-support");
    assert.equal(slug("  R&D / Labs  "), "r-d-labs");
    assert.equal(slug("Café Nord"), "cafe-nord");
    assert.equal(slug("🚀"), "");
});

test("somewhere in a Lemma is spelled in names, and reads back to the same place", () => {
    const places: [View, string][] = [
        [{ name: "outcome", podId: SALES, tab: "home" }, "/l/acme-inc/sales"],
        [{ name: "app", podId: SALES, app: "deals" }, "/l/acme-inc/sales/apps/deals"],
        [{ name: "app", podId: SALES, app: "people", at: "/1042" }, "/l/acme-inc/sales/apps/people/1042"],
        [{ name: "app", podId: SUPPORT, app: "follow-ups", at: "/by owner/r 9" }, "/l/acme-inc/customer-support/apps/follow-ups/by%20owner/r%209"],
        /* An app may take a word one of the Lemma's own sections goes by:
           `apps/` in front of it is what tells the two apart. */
        [{ name: "app", podId: SALES, app: "people" }, "/l/acme-inc/sales/apps/people"],
        [{ name: "outcome", podId: SALES, tab: "people" }, "/l/acme-inc/sales/people"],
        [{ name: "outcome", podId: SALES, tab: "chats" }, "/l/acme-inc/sales/chats"],
        [{ name: "outcome", podId: SALES, tab: "apps" }, "/l/acme-inc/sales/apps"],
        [{ name: "outcome", podId: SALES, tab: "about" }, "/l/acme-inc/sales/about"],
        [{ name: "table", podId: SALES, table: "people" }, "/l/acme-inc/sales/tables/people"],
        [{ name: "record", podId: SALES, table: "people", recordId: "r 9" }, "/l/acme-inc/sales/tables/people/r%209"],
        [{ name: "chats", open: { kind: "chat", podId: SALES, id: "c1" } }, "/l/acme-inc/sales/chats/c1"],
        [{ name: "file", podId: SALES, path: "/reports/Q3 review.md" }, "/l/acme-inc/sales/files/reports/Q3%20review.md"],
    ];
    for (const [view, spelled] of places) {
        assert.equal(named(writeView(view), ACME), spelled);
        assert.ok(looksNamed(spelled), spelled + " is told from an id address");
        const own = unnamed(spelled, ACME);
        assert.ok(own, spelled + " reads back");
        assert.deepEqual(readView(own), view, spelled);
    }
});

test("an address that names would not read back keeps its id", () => {
    const kept: View[] = [
        /* Not in a Lemma at all. */
        { name: "today" },
        { name: "apps" },
        { name: "outcomes" },
        { name: "hire" },
        /* Lemmas whose names cannot be the word after the organization's. */
        { name: "outcome", podId: "p-chats", tab: "home" },
        { name: "outcome", podId: "p-ops-1", tab: "home" },
        { name: "outcome", podId: "p-ops-2", tab: "chats" },
        { name: "outcome", podId: "p-emoji", tab: "home" },
        /* One this organization does not have. */
        { name: "outcome", podId: "somewhere-else", tab: "home" },
    ];
    for (const view of kept) {
        const own = writeView(view);
        assert.equal(named(own, ACME), own, own);
    }
    assert.equal(named("/l/" + SALES, { org: "🚀", lemmas: ACME.lemmas }), "/l/" + SALES, "an organization with nothing to spell");
});

test("an id address, and an old one, are not taken for names", () => {
    for (const path of ["/l", "/l/" + SALES, "/l/" + SALES + "/chats", "/l/" + SALES + "/apps/people/1042", "/l/" + SALES + "/tables/people/r1", "/home", "/apps", "/"]) {
        assert.equal(looksNamed(path), false, path);
        assert.equal(unnamed(path, ACME), null, path);
    }
    /* Words only the workspace used look like names until they are read
       against the ones there are, and then are nobody's. */
    for (const path of ["/l/" + SALES + "/conversation/c1", "/l/" + SALES + "/table/leads", "/l/other-org/sales/apps/people", "/l/acme-inc/nobody/apps/people", "/l/acme-inc/ops/apps/people", "/l/acme-inc/chats"]) {
        assert.equal(unnamed(path, ACME), null, path);
    }
});

test("what a named address says after the Lemma is what it is, without whose", () => {
    assert.equal(namedTail("/l/acme-inc/sales"), "");
    assert.equal(namedTail("/l/acme-inc/sales/apps/people/1042"), "/apps/people/1042");
    assert.equal(namedTail("/l/" + SALES + "/chats"), null);
    assert.equal(namedTail("/home"), null);
});

test("analytics is told what kind of place a named address is, and never which", () => {
    assert.equal(toRouteTemplate("/l/acme-inc/sales"), "/l/[org]/[lemma]");
    assert.equal(toRouteTemplate("/l/acme-inc/sales/apps/people"), "/l/[org]/[lemma]/apps/[name]");
    assert.equal(toRouteTemplate("/l/acme-inc/sales/apps/people/1042"), "/l/[org]/[lemma]/apps/[name]/[...path]");
    assert.equal(toRouteTemplate("/l/acme-inc/sales/chats"), "/l/[org]/[lemma]/chats");
    assert.equal(toRouteTemplate("/l/acme-inc/sales/tables/people/r1"), "/l/[org]/[lemma]/tables/[table]/[id]");
    assert.equal(toRouteTemplate("/l/acme-inc/sales/nothing/it/reads"), "/l/[org]/[lemma]/[...rest]");
    /* By id, as before, with where the app is inside itself taken out too. */
    assert.equal(toRouteTemplate("/l/" + SALES + "/apps/people"), "/l/[lemma]/apps/[name]");
    assert.equal(toRouteTemplate("/l/" + SALES + "/apps/people/1042"), "/l/[lemma]/apps/[name]/[...path]");
    for (const path of ["/l/acme-inc/sales", "/l/acme-inc/sales/apps/people/1042", "/l/acme-inc/sales/nothing/it/reads"]) {
        const template = toRouteTemplate(path);
        for (const word of ["acme", "sales", "people", "1042", "nothing"]) {
            if (word === "people" && path.includes("/tables/")) continue;
            assert.ok(!template.includes(word), template + " names " + word);
        }
    }
});
