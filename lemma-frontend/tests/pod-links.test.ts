import test from "node:test";
import assert from "node:assert/strict";
import { lemma } from "../src/session/client.ts";
import { liveSource } from "../src/data/live.ts";
import type { AskablePod, LibraryItem, PodLink } from "../src/data/types.ts";
import {
    askerChoices,
    everyPage,
    isPersonal,
    seededShares,
    shareRows,
    sharedLine,
    sharesNotListed,
    toggled,
} from "../src/space/pod-links.ts";

/** Connecting one teammate to another, pinned where it went wrong: connecting
 *  again replaces every share, so a dialog that started empty took away what
 *  the other teammate read; and an owner was offered teammates they are not
 *  in, which the server then refused. */

const askable = (id: string, throughYou: boolean, connected = false): AskablePod => ({ id, name: id, iconUrl: null, throughYou, connected });
const link = (podId: string, shared: PodLink["shared"]): PodLink => ({ podId, name: podId, iconUrl: null, shared });
const item = (partial: Partial<LibraryItem> & Pick<LibraryItem, "kind" | "path">): LibraryItem => ({
    id: partial.path, name: partial.path.split("/").pop() || partial.path, updated: "", detail: "", ...partial,
});

test("only teammates the person is in are offered, and connected ones say so", () => {
    const choices = askerChoices(
        [askable("support", true), askable("finance", false, true), askable("sales", true)],
        [link("sales", [])],
    );
    assert.deepEqual(choices.map((one) => [one.id, one.connected]), [["support", false], ["sales", true]]);
});

test("choosing a connected teammate starts from what it reads already", () => {
    const links = [link("sales", [{ kind: "table", name: "invoices" }, { kind: "folder", name: "/reports/2026" }])];
    assert.deepEqual(seededShares(links, "sales"), links[0].shared);
    assert.deepEqual(seededShares(links, "support"), []);
});

test("a share in no list on screen is kept, not dropped", () => {
    const chosen = [{ kind: "table" as const, name: "invoices" }, { kind: "folder" as const, name: "/reports/2026" }];
    assert.deepEqual(sharesNotListed(chosen, [{ kind: "table", name: "invoices" }]), [{ kind: "folder", name: "/reports/2026" }]);
    assert.deepEqual(toggled(chosen, { kind: "table", name: "invoices" }), [{ kind: "folder", name: "/reports/2026" }]);
    assert.deepEqual(toggled([], { kind: "table", name: "invoices" }), [{ kind: "table", name: "invoices" }]);
});

test("the person's own things are never offered, and Public ones are marked", () => {
    assert.equal(isPersonal({ path: "/me" }), true);
    assert.equal(isPersonal({ path: "/me/notes" }), true);
    assert.equal(isPersonal({ path: "/media" }), false);
    const rows = shareRows([
        item({ kind: "folder", path: "/me" }),
        item({ kind: "folder", path: "/drafts", visibility: "PERSONAL" }),
        item({ kind: "folder", path: "/reports", visibility: "PUBLIC" }),
        item({ kind: "file", path: "/report.pdf" }),
        item({ kind: "table", path: "invoices", name: "invoices", visibility: "POD" }),
        item({ kind: "table", path: "mine", name: "mine", visibility: "PERSONAL" }),
    ]);
    assert.deepEqual(rows, [
        { share: { kind: "folder", name: "/reports" }, public: true, folder: true },
        { share: { kind: "table", name: "invoices" }, public: false, folder: false },
    ]);
});

test("every page of a listing is read, and a repeated token cannot loop", async () => {
    const pages: Record<string, { items: number[]; next: string | null }> = {
        "": { items: [1, 2], next: "b" },
        b: { items: [3], next: "c" },
        c: { items: [4], next: "b" },
    };
    assert.deepEqual(await everyPage(async (page) => pages[page ?? ""]), [1, 2, 3, 4]);
});

test("what a connection reads is said in a line", () => {
    assert.equal(sharedLine([]), "Reads only what is Public here");
    assert.equal(sharedLine([{ kind: "table", name: "invoices" }, { kind: "folder", name: "/reports" }]), "Reads invoices, /reports");
});

test("connections and askable teammates are read off the wire as the page uses them", async (context) => {
    const id = "pod-links-wire";
    const client = lemma(id);
    context.mock.method(client, "request", async (_method: string, path: string) => {
        if (path.endsWith("/pod-links")) {
            return {
                items: [
                    {
                        pod_id: "sales", name: "Sales", icon_url: null, steward_name: "Priya",
                        shared: [
                            { resource_type: "datastore_table", resource_name: "invoices" },
                            { resource_type: "folder", resource_name: "/reports" },
                            { resource_type: "folder" },
                        ],
                    },
                    { name: "No id" },
                ],
            };
        }
        return { items: [{ pod_id: "sales", name: "Sales", through_you: true, connected: false }, { pod_id: "x" }] };
    });
    assert.deepEqual(await liveSource.podLinks(id), [{
        podId: "sales", name: "Sales", iconUrl: null, stewardName: "Priya",
        shared: [{ kind: "table", name: "invoices" }, { kind: "folder", name: "/reports" }],
    }]);
    assert.deepEqual(await liveSource.askablePods(id), [{ id: "sales", name: "Sales", iconUrl: null, throughYou: true, connected: false }]);
});

test("connecting sends every share, so connecting again replaces them", async (context) => {
    const id = "pod-links-put";
    const client = lemma(id);
    const sent: unknown[] = [];
    context.mock.method(client, "request", async (method: string, path: string, options?: { body?: unknown }) => {
        sent.push([method, path, options?.body]);
        return {};
    });
    await liveSource.connectPod(id, "sales", [{ kind: "table", name: "invoices" }, { kind: "folder", name: "/reports" }]);
    assert.deepEqual(sent, [["PUT", `/pods/${id}/pod-links/sales`, {
        shares: [
            { resource_type: "datastore_table", resource_name: "invoices", permission_ids: ["datastore.table.read", "datastore.record.read"] },
            { resource_type: "folder", resource_name: "/reports", permission_ids: ["folder.read"] },
        ],
    }]]);
});
