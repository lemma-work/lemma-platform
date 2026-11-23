import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { docsPageMap, docsPages, type DocsPage } from "../src/site/data/docs.ts";
import {
    buildDocsSearchIndex,
    docsInOrder,
    docsNeighbours,
    docsPieces,
    docsSections,
    quickstartSteps,
    shellCommands,
    shellLines,
} from "../src/site/docs-content.ts";
import { markMatches, searchDocs, suggestedDocs } from "../src/site/docs-search.ts";
import {
    changelogEntry,
    desktopEntry,
    splitNotes,
    updateHeading,
} from "../src/site/changelog-notes.ts";
import type { ContentDoc } from "../src/site/content/types.ts";

const index = buildDocsSearchIndex(docsInOrder());

test("the guide list and the search hold every page, once", () => {
    const listed = docsInOrder().map((page) => page.slug);
    assert.equal(new Set(listed).size, docsPages.length);
    assert.equal(index.filter((hit) => hit.kind === "page").length, docsPages.length);
    assert.equal(docsNeighbours(listed[0]).previous, null);
    assert.equal(docsNeighbours(listed[1]).previous?.slug, listed[0]);
});

test("search finds pages, the sections in them, and their commands", () => {
    const groups = searchDocs(index, "auth login");
    const commands = groups.find((g) => g.kind === "command")?.hits ?? [];
    assert.equal(commands[0]?.title, "lemma auth login");
    assert.match(commands[0].href, /^\/docs\/getting-started#cloud-quickstart$/);

    const sections = searchDocs(index, "local stack").find((g) => g.kind === "section");
    assert.equal(sections?.hits[0].href, "/docs/getting-started#local-stack-quickstart");

    const pages = searchDocs(index, "quickstart").find((g) => g.kind === "page");
    assert.equal(pages?.hits[0].href, "/docs/getting-started");

    assert.deepEqual(searchDocs(index, "nothing-is-called-this"), []);
    assert.ok(suggestedDocs(index)[0].hits.length > 0);
    /* `cd` is not something anybody searches for. */
    assert.ok(!index.some((hit) => hit.kind === "command" && hit.title.startsWith("cd ")));
});

test("a section's id in search is the id its heading has on the page", () => {
    for (const page of docsPages) {
        const ids = new Set(docsSections(page).map((s) => s.id));
        for (const hit of index.filter((h) => h.kind !== "page" && h.href.startsWith(`/docs/${page.slug}#`)))
            assert.ok(ids.has(hit.href.split("#")[1]), hit.href);
    }
});

test("matches are marked, case and all", () => {
    assert.deepEqual(markMatches("Lemma auth login", "AUTH"), [
        { text: "Lemma ", match: false },
        { text: "auth", match: true },
        { text: " login", match: false },
    ]);
});

test("a shell block gets a prompt per command, not per row", () => {
    const lines = shellLines(`# set up\nlemma table create --payload '{\n  "name": "tickets"\n}'\n\nlemma pod describe \\\n  --json`);
    assert.deepEqual(
        lines.map((l) => l.kind),
        ["comment", "command", "more", "more", "blank", "command", "more"],
    );
    assert.deepEqual(shellCommands("npm install lemma-sdk\n# then\nuv pip install lemma-sdk"), [
        "npm install lemma-sdk",
        "uv pip install lemma-sdk",
    ]);
});

test("the same step in two languages becomes tabs; different steps stay apart", () => {
    const page: DocsPage = {
        slug: "x",
        title: "X",
        eyebrow: "",
        group: "SDK",
        description: "",
        blocks: [
            { type: "code", title: "Install (TypeScript)", language: "bash", code: "npm install lemma-sdk" },
            { type: "code", title: "Install (Python)", language: "python", code: "uv pip install lemma-sdk" },
            { type: "code", title: "Use the client", language: "ts", code: "new LemmaClient()" },
        ],
    };
    const pieces = docsPieces(page);
    assert.equal(pieces.length, 2);
    assert.equal(pieces[0].kind, "tabs");
    assert.deepEqual(docsSections(page).map((s) => s.title), ["Install", "Use the client"]);
    /* The Quickstart's TypeScript and Python blocks are different steps. */
    const quickstart = docsPageMap.get("getting-started")!;
    assert.ok(docsPieces(quickstart).every((piece) => piece.kind === "block"));
});

test("the docs home's first steps are the Quickstart's own commands", () => {
    assert.deepEqual(
        quickstartSteps().map((s) => s.command),
        ["uv tool install lemma-terminal", "lemma auth login", "lemma pod create my-team --with-starter"],
    );
});

function doc(slug: string, body: string, frontmatter: Partial<ContentDoc["frontmatter"]> = {}): ContentDoc {
    return {
        collection: "changelog",
        slug,
        source: `content/changelog/${slug}.mdx`,
        body,
        frontmatter: {
            title: "1.0.0",
            description: "A release.",
            published: "2026-10-28",
            tags: ["release"],
            draft: false,
            ...frontmatter,
        },
    };
}

test("an entry's developer section moves to its panel; the rest stay in order", () => {
    const entry = changelogEntry(
        doc("1-0-0", "## What changes for API, SDK and CLI users\n\nRoutes are gone.\n\n## Voice calls\n\nCall it.\n\n```bash\n## not a heading\n```\n\n## Desktop\n\nFaster.", { breaking: true, desktop: true }),
    );
    assert.equal(entry.heading, "Lemma 1.0.0");
    assert.equal(entry.date, "28 Oct 2026");
    assert.deepEqual(entry.sections, ["Voice calls", "Desktop"]);
    assert.equal(entry.developer, "Routes are gone.");
    assert.equal(entry.developerId, "what-changes-for-api-sdk-and-cli-users");
    assert.match(entry.notes, /^### Voice calls/);
    assert.match(entry.notes, /## not a heading/);
    assert.ok(entry.breaking && entry.desktop);

    const update = changelogEntry(
        doc("2026-09-16-x", "Intro words.\n\n## Widgets\n\nMore.", {
            title: "September 16 — Editable widgets",
            tags: ["development-update"],
            breaking: true,
        }),
    );
    assert.equal(update.kind, "update");
    assert.equal(update.heading, "Editable widgets");
    assert.equal(update.developer, null);
    assert.equal(update.breaking, false, "Breaking needs a developer section to belong to");
    assert.equal(updateHeading("Browser handoff"), "Browser handoff");
});

test("a developer section under another heading is named in the frontmatter", () => {
    const entry = changelogEntry(doc("0-8-0", "## Three names\n\nRenamed.\n\n## Windows\n\nYes.", { developer: "Three names" }));
    assert.equal(entry.developer, "Renamed.");
    assert.deepEqual(entry.sections, ["Windows"]);
});

test("only the newest release with a Desktop build offers the download", () => {
    const entries = [
        changelogEntry(doc("b", "", { title: "2.0.0" })),
        changelogEntry(doc("a", "", { title: "1.0.0", desktop: true })),
        changelogEntry(doc("z", "", { title: "0.9.0", desktop: true })),
    ];
    assert.equal(desktopEntry(entries), "a");
});

test("the real notes split where their headings are", () => {
    const body = readFileSync(new URL("../content/changelog/0-10-0.mdx", import.meta.url), "utf8").split(/^---$/m).slice(2).join("---");
    const { sections } = splitNotes(body);
    assert.equal(sections[0].title, "What changes for API, SDK and CLI users");
    assert.ok(sections.length > 3);
});
