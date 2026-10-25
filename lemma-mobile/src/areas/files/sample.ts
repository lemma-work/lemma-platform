import * as FileSystem from "expo-file-system/legacy";
import { SAMPLE_LEMMAS } from "../../data/sample";
import { hitsOf, kindOf, nameOf, parentOf, readUrlOf, snippetOf } from "./model";
import { SAMPLE_PDF, SAMPLE_PNG } from "./sample-media";
import type { CommentRow, FileContent, FilesSource, LibraryItem, SharedLink } from "./source";
import { readComment } from "@web/docpages/comments/model";
import { PAGE_TEMPLATES } from "@web/docpages/templates";
import { SAMPLE_MEMORY_FILES } from "../admin/memory/sample";
import { SAMPLE_SKILL_FILES } from "../admin/skills/sample";

/** Files, pages and docs, as sample data: shaped like a real Lemma, never a
 *  real one. Every sample Lemma starts with the same small tree, held in
 *  memory and changed by what the person does to it, so uploads, renames,
 *  pages, comments and links all work without a server, until the app
 *  restarts. */

interface Entry {
    item: LibraryItem;
    text?: string;
    /** Bytes as base64, for what is drawn from bytes. */
    base64?: string;
    /** A file on this phone, for what the person uploaded here. */
    uri?: string;
}

const DAY = 86_400_000;

function at(ago: number): string {
    return new Date(Date.now() - ago).toISOString();
}

function file(path: string, mime: string, ago: number, contents: { text?: string; base64?: string; size?: number }): Entry {
    const size = contents.size ?? (contents.text !== undefined ? contents.text.length : Math.round(((contents.base64 ?? "").length * 3) / 4));
    return {
        item: { id: path, name: nameOf(path), kind: "file", path, updated: at(ago), detail: mime, mime, size },
        ...(contents.text !== undefined ? { text: contents.text } : {}),
        ...(contents.base64 !== undefined ? { base64: contents.base64 } : {}),
    };
}

/** A file whose reading failed, for the row that offers Retry. */
function failed(one: Entry): Entry {
    return { ...one, item: { ...one.item, status: "FAILED" } };
}

function folder(path: string, ago: number): Entry {
    return { item: { id: path, name: nameOf(path), kind: "folder", path, updated: at(ago), detail: "Folder" } };
}

const WELCOME = `# Welcome

How this Lemma works, in one page.

## Each morning

- Read the queue
- Answer what is waiting
- [ ] Check the refunds over $200
- [x] Send the weekly numbers

| Who | Holds |
|---|---|
| Maria | Refunds |
| Dev | Shipping |

> Every customer hears back within a day.

\`\`\`lemma-view
-- Orders this week
SELECT id, customer, total, status FROM "orders" ORDER BY total DESC
-- spec: {"table":"orders","columns":["id","customer","total","status"],"filters":[],"group":null,"sort":{"column":"total","dir":"desc"},"limit":50}
\`\`\`
`;

const PRICES = `<!doctype html><html><body style="font:15px system-ui;padding:16px;color:var(--lemma-widget-text,#1f1f1f)">
<h1 style="font-weight:500">Prices</h1><p>Lamps from <b>$60</b>. Shipping is free over $200.</p>
<ul><li>Desk lamp: $60</li><li>Floor lamp: $140</li><li>Pendant: $95</li></ul></body></html>`;

function seed(lemmaName: string): Map<string, Entry> {
    const guide = PAGE_TEMPLATES.find((one) => one.id === "guide")!;
    const entries = [
        folder("/pages", 2 * DAY),
        file("/pages/Your guide to pages.md", "text/markdown", 2 * DAY, { text: guide.body(lemmaName, new Date()) }),
        file("/pages/Weekly update.md", "text/markdown", 3 * 3_600_000, { text: PAGE_TEMPLATES.find((one) => one.id === "weekly")!.body(lemmaName, new Date()) }),
        folder("/Notes", 5 * DAY),
        file("/Notes/Welcome.md", "text/markdown", 4 * DAY, { text: WELCOME }),
        file("/Notes/Prices.html", "text/html", 6 * DAY, { text: PRICES }),
        file("/Notes/Numbers.csv", "text/csv", 1 * DAY, { text: "week,answered,asked\n41,31,44\n42,38,41\n43,40,42\n" }),
        file("/Notes/Settings.json", "application/json", 9 * DAY, { text: JSON.stringify({ reply_within_hours: 24, signature: "The team" }, null, 2) }),
        file("/Notes/Brief.pdf", "application/pdf", 2 * DAY, { base64: SAMPLE_PDF }),
        file("/Notes/Mark.png", "image/png", 7 * DAY, { base64: SAMPLE_PNG }),
        file("/Notes/Contract.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 12 * DAY, { base64: "", size: 48_210 }),
        failed(file("/Notes/Scan.pdf", "application/pdf", 20 * 60_000, { base64: SAMPLE_PDF })),
        folder("/me", 30 * DAY),
        file("/me/Draft.md", "text/markdown", 3 * DAY, { text: "# My draft\n\nOnly I can see this page.\n" }),
    ];
    return new Map(entries.map((entry) => [entry.item.path, entry]));
}

const TABLES: Record<string, Record<string, unknown>[]> = {
    orders: [
        { id: "1041", customer: "Northwind", total: 240, status: "Refund asked" },
        { id: "1042", customer: "Contoso", total: 60, status: "Shipped" },
        { id: "1043", customer: "Fabrikam", total: 380, status: "Paid" },
        { id: "1044", customer: "Tailspin", total: 140, status: "Shipped" },
    ],
    customers: [
        { id: "c1", name: "Northwind", city: "Leeds", since: "2024-03-02" },
        { id: "c2", name: "Contoso", city: "Bristol", since: "2025-01-19" },
        { id: "c3", name: "Fabrikam", city: "York", since: "2023-11-08" },
    ],
};

class SampleOnly extends Error {}

const lemmaName = (podId: string) => SAMPLE_LEMMAS.find((one) => one.id === podId)?.name ?? "the Lemma";

export function filesSample(): FilesSource {
    const pods = new Map<string, Map<string, Entry>>();
    const comments = new Map<string, CommentRow[]>();
    const links = new Map<string, (SharedLink & { path: string })[]>();

    const tree = (podId: string) => {
        let held = pods.get(podId);
        if (!held) {
            held = seed(lemmaName(podId));
            /* The skills and memory notes the settings area shows for this
               Lemma, so opening one from there lands on its page. */
            for (const one of [...(SAMPLE_SKILL_FILES[podId] ?? []), ...(SAMPLE_MEMORY_FILES[podId] ?? [])]) {
                const ago = Math.max(0, Date.now() - Date.parse(one.updated));
                for (let up = parentOf(one.path); up !== "/" && !held.has(up); up = parentOf(up)) held.set(up, folder(up, ago));
                held.set(one.path, file(one.path, "text/markdown", ago, { text: one.text }));
            }
            pods.set(podId, held);
        }
        return held;
    };
    const entry = (podId: string, path: string) => {
        const found = tree(podId).get(path);
        if (!found) throw Object.assign(new Error(`${nameOf(path)} is not there any more.`), { statusCode: 404 });
        return found;
    };
    const put = (podId: string, next: Entry) => {
        const files = tree(podId);
        /* Folders on the way are made, as an upload makes them. */
        for (let up = parentOf(next.item.path); up !== "/" && !files.has(up); up = parentOf(up)) files.set(up, folder(up, 0));
        files.set(next.item.path, next);
        return next.item;
    };

    return {
        async list(podId, directory) {
            const items = [...tree(podId).values()].map((one) => one.item).filter((item) => parentOf(item.path) === directory);
            return { items, next: null };
        },

        async search(podId, query) {
            const needle = query.trim().toLowerCase();
            const found = [...tree(podId).values()]
                .filter((one) => one.text !== undefined && (one.text.toLowerCase().includes(needle) || one.item.name.toLowerCase().includes(needle)))
                .map((one) => {
                    const text = one.text ?? "";
                    const at = Math.max(0, text.toLowerCase().indexOf(needle) - 60);
                    return { path: one.item.path, content: snippetOf(text.slice(at, at + 220)), score: text.toLowerCase().includes(needle) ? 1 : 0.5 };
                });
            return hitsOf(found);
        },

        async read(podId, path) {
            const one = entry(podId, path);
            const mime = one.item.mime ?? "";
            const kind = kindOf(mime, path);
            const base: FileContent = { name: one.item.name, path, mime, size: one.item.size ?? 0, kind };
            if (one.text !== undefined) return { ...base, text: one.text };
            if (one.uri) {
                if (kind === "markdown" || kind === "text" || kind === "html") return { ...base, text: await FileSystem.readAsStringAsync(one.uri) };
                return { ...base, rawUrl: one.uri };
            }
            if (kind === "image" && one.base64) return { ...base, rawUrl: `data:${mime};base64,${one.base64}` };
            if (kind === "video" || kind === "audio") return { ...base, note: "Sample data has no recording to play." };
            return base;
        },

        async bytes(podId, path) {
            const one = entry(podId, path);
            if (one.uri) return FileSystem.readAsStringAsync(one.uri, { encoding: FileSystem.EncodingType.Base64 });
            return one.base64 ?? "";
        },

        async stamp(podId, path) {
            return { sha: null, updatedAt: entry(podId, path).item.updated };
        },

        async signedUrl(podId, path) {
            const one = entry(podId, path);
            return one.uri ?? (one.base64 ? `data:${one.item.mime};base64,${one.base64}` : null);
        },

        async why() {
            return "Lemma could not read this file. Retry, or upload it again.";
        },

        async toPhone(podId, path) {
            const one = entry(podId, path);
            if (one.uri) return one.uri;
            const uri = `${FileSystem.cacheDirectory}sample-${Date.now()}-${one.item.name.replace(/[^\w.\- ]+/g, "_")}`;
            if (one.text !== undefined) await FileSystem.writeAsStringAsync(uri, one.text);
            else await FileSystem.writeAsStringAsync(uri, one.base64 ?? "", { encoding: FileSystem.EncodingType.Base64 });
            return uri;
        },

        async upload(podId, directory, picked) {
            const path = (directory === "/" ? "" : directory) + "/" + picked.name;
            return put(podId, { item: { id: path, name: picked.name, kind: "file", path, updated: at(0), detail: picked.type, mime: picked.type, size: picked.size ?? undefined }, uri: picked.uri });
        },

        async uploadBytes(podId, directory, picked) {
            const path = (directory === "/" ? "" : directory) + "/" + picked.name;
            return put(podId, { item: { id: path, name: picked.name, kind: "file", path, updated: at(0), detail: picked.type, mime: picked.type, size: Math.round((picked.base64.length * 3) / 4) }, base64: picked.base64 });
        },

        async makeFolder(podId, parent, name) {
            const path = (parent === "/" ? "" : parent) + "/" + name;
            if (tree(podId).has(path)) throw Object.assign(new Error(`${name} is already here.`), { statusCode: 409 });
            return put(podId, folder(path, 0));
        },

        async move(podId, from, to) {
            const files = tree(podId);
            if (files.has(to)) throw Object.assign(new Error(`${nameOf(to)} is already there.`), { statusCode: 409 });
            entry(podId, from);
            for (const [path, one] of [...files]) {
                if (path !== from && !path.startsWith(from + "/")) continue;
                files.delete(path);
                const moved = to + path.slice(from.length);
                files.set(moved, { ...one, item: { ...one.item, id: moved, path: moved, name: nameOf(moved), updated: path === from ? at(0) : one.item.updated } });
            }
        },

        async remove(podId, path) {
            const files = tree(podId);
            for (const held of [...files.keys()]) if (held === path || held.startsWith(path + "/")) files.delete(held);
        },

        async retry(podId, path) {
            const one = entry(podId, path);
            tree(podId).set(path, { ...one, item: { ...one.item, status: "READY" } });
            return "READY";
        },

        async writeText(podId, path, text) {
            const files = tree(podId);
            const was = files.get(path);
            put(podId, { item: { ...(was?.item ?? { id: path, name: nameOf(path), kind: "file", path, detail: "text/markdown", mime: "text/markdown" }), updated: at(0), size: text.length }, text });
        },

        async createText(podId, path, text) {
            if (tree(podId).has(path)) throw Object.assign(new Error(`${nameOf(path)} is already there.`), { statusCode: 409 });
            put(podId, { item: { id: path, name: nameOf(path), kind: "file", path, updated: at(0), detail: "text/markdown", mime: /\.md$/i.test(path) ? "text/markdown" : "text/plain", size: text.length }, text });
        },

        async tableNames() {
            return Object.keys(TABLES);
        },

        async tableColumns(_podId, table) {
            return Object.keys(TABLES[table]?.[0] ?? {}).map((name) => ({ name }));
        },

        async tableRows(_podId, table) {
            return { items: TABLES[table] ?? [], next: null };
        },

        async runQuery(_podId, sql) {
            const table = /\bfrom\s+"?([A-Za-z_][A-Za-z0-9_]*)"?/i.exec(sql)?.[1] ?? "";
            if (!TABLES[table]) throw new Error("Sample data has two tables, orders and customers.");
            return { items: TABLES[table], truncated: false };
        },

        async appUrl(podId, path) {
            return `https://lemma.work/pod/${podId}/files?file=${encodeURIComponent(path)}`;
        },

        async links(podId, path) {
            return (links.get(podId) ?? []).filter((one) => one.path === path);
        },

        async share(podId, path, bounds) {
            const code = Math.random().toString(36).slice(2, 12);
            const made = { code, path, readUrl: readUrlOf("https://lemma.work", code), expiresAt: new Date(Date.now() + bounds.expiresSeconds * 1000).toISOString(), maxHits: bounds.maxHits };
            links.set(podId, [made, ...(links.get(podId) ?? [])]);
            return made;
        },

        async revoke(podId, code) {
            const held = links.get(podId) ?? [];
            links.set(podId, held.filter((one) => one.code !== code));
            return held.some((one) => one.code === code);
        },

        async pageConversation() {
            return null;
        },

        async movePageConversation() {},

        async askAboutPage() {
            throw new SampleOnly("Sample data sends nothing. Sign in to ask a Lemma.");
        },

        async commentsStatus() {
            return "ready";
        },

        async enableComments() {},

        async comments(podId, path) {
            return (comments.get(podId) ?? []).filter((row) => row.filePath === path);
        },

        async addComment(podId, data, author) {
            const row = readComment({ ...data, id: "c" + Date.now().toString(36), written_by: author.id, author_name: author.name, created_at: new Date().toISOString() });
            if (!row) throw new Error("That comment could not be kept.");
            comments.set(podId, [...(comments.get(podId) ?? []), row]);
            return row;
        },

        async updateComment(podId, id, patch) {
            comments.set(podId, (comments.get(podId) ?? []).map((row) => row.id !== id ? row : {
                ...row,
                ...(patch.resolved !== undefined ? { resolved: patch.resolved === true } : {}),
                ...(typeof patch.body === "string" ? { body: patch.body } : {}),
            }));
        },

        async deleteComment(podId, id) {
            comments.set(podId, (comments.get(podId) ?? []).filter((row) => row.id !== id));
        },

        async moveComments(podId, from, to) {
            comments.set(podId, (comments.get(podId) ?? []).map((row) => (row.filePath === from ? { ...row, filePath: to } : row)));
        },

        async mentionable(podId, name) {
            return [
                { kind: "agent", key: "POD_DEFAULT", label: name },
                { kind: "person", key: "sample-maria", label: "Maria" },
                { kind: "person", key: "sample-dev", label: "Dev" },
            ];
        },

        async notify() {},
        async wakes() {
            return [];
        },
        async wakeRuns() {
            return [];
        },
        async turnOnWake() {
            throw new SampleOnly("Sample data has no agents to wake. Sign in to ask one.");
        },
        async askAgain() {},
    };
}
