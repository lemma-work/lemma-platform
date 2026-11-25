import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { docsPageMap, docsPages, type DocsPage } from "../src/site/data/docs.ts";
import { runItYourselfPage, useLemmaPages } from "../src/site/data/use-docs.ts";
import { markdownForPath } from "../src/site/markdown/pages.ts";
import { llmsTxt } from "../src/site/markdown/llms-txt.ts";
import {
    buildDocsSearchIndex,
    docsCrumbs,
    docsDoorOf,
    docsInOrder,
    docsNavFor,
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

test("the half for people using Lemma is written: six pages, and where it runs", () => {
    const use = docsNavFor("use");
    assert.deepEqual(use.map((group) => group.title), ["Use Lemma", "Set up"]);
    assert.deepEqual(
        use[0].pages.map((page) => page.title),
        ["Lemmas", "Apps", "Automations", "Where to reach a Lemma", "People", "Connectors"],
    );
    assert.deepEqual(use[1].pages, [{ slug: "run-it-yourself", title: "Run it yourself" }]);
    /* Each half lists its own pages and none of the other's. */
    const build = docsNavFor("build").flatMap((group) => group.pages.map((page) => page.slug));
    assert.ok(build.includes("getting-started"));
    assert.ok(!build.some((slug) => docsDoorOf(slug) === "use"));
    assert.equal(docsDoorOf("use/apps"), "use");
    assert.equal(docsDoorOf("cli/apps"), "build");
    assert.equal(docsDoorOf(""), null);
    assert.deepEqual(docsCrumbs(docsPageMap.get("use/apps")!), ["Use Lemma"]);
    assert.deepEqual(docsCrumbs(runItYourselfPage), ["Use Lemma", "Set up"]);
    assert.deepEqual(docsCrumbs(docsPageMap.get("cli/apps")!), ["Build on Lemma", "CLI"]);
    /* Search, the Markdown index and llms.txt each hold every one of them. */
    const docsIndex = markdownForPath("/docs") ?? "";
    for (const page of [...useLemmaPages, runItYourselfPage]) {
        const href = "/docs/" + page.slug;
        assert.equal(docsPageMap.get(page.slug), page);
        assert.equal(searchDocs(index, page.title).find((g) => g.kind === "page")?.hits[0].href, href, page.title);
        assert.ok(docsIndex.includes(`](${href})`), href);
        assert.ok(llmsTxt().includes(href + ")"), href);
    }
});

test("the pages for people using Lemma keep to the app's own words", () => {
    /* A Lemma is a Lemma: never the API's word for it, nor an older one. */
    const banned = /\b(pods?|teammates?|bots?|workspaces?)\b/i;
    for (const page of useLemmaPages) {
        const said = [page.title, page.description];
        for (const block of page.blocks) {
            if (block.title) said.push(block.title);
            if ("body" in block && block.body) said.push(block.body);
            if (block.type === "list" || block.type === "steps") said.push(...block.items);
            if (block.type === "links") for (const item of block.items) said.push(item.label, item.note ?? "");
            if (block.type === "table") said.push(...block.columns, ...block.rows.flat());
            assert.notEqual(block.type, "code", page.slug + " is not for developers");
        }
        for (const line of said) assert.doesNotMatch(line, banned, page.slug);
    }
});

test("a links block is followed, on the page and in Markdown", () => {
    const markdown = markdownForPath("/docs/run-it-yourself") ?? "";
    for (const guide of ["installation", "self-hosting", "configuration", "observability"])
        assert.ok(
            markdown.includes(`](https://github.com/lemma-work/lemma-platform/blob/main/docs/${guide}.md)`),
            guide,
        );
    assert.match(markdownForPath("/docs/use/lemmas") ?? "", /- \[Apps\]\(\/docs\/use\/apps\): /);
    /* Every link to another docs page names one that exists. */
    for (const page of docsPages)
        for (const block of page.blocks)
            if (block.type === "links")
                for (const item of block.items)
                    if (item.href.startsWith("/docs/"))
                        assert.ok(docsPageMap.has(item.href.slice("/docs/".length)), item.href);
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
