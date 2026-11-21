import GithubSlugger from "github-slugger";
import {
    docsGroups,
    docsPageMap,
    docsPages,
    type DocsBlock,
    type DocsPage,
} from "@/site/data/docs";
import {
    conceptDocsPages,
    glossaryStub,
    howLemmaWorksStub,
} from "@/site/data/concept-docs";
import { educationGuidePages } from "@/site/data/education-guides";
import { githubUrl } from "@/site/links";
import type { DocsHit } from "@/site/docs-search";

/* How the docs pages are read: their order, their sections, their code.
   Kept apart from the components so the search index, the outline and the
   tests all cut a page the same way. */

/** The half of the docs for people using Lemma, planned and not yet
 *  written. Listed as being written; never linked. */
export const USE_LEMMA_TOPICS = [
    "Lemmas",
    "Apps",
    "Automations",
    "Where to reach a Lemma",
    "People",
    "Connectors",
] as const;

export type DocsNavPage = { slug: string; title: string };
export type DocsNavGroup = { title: string; pages: DocsNavPage[] };

/** Every page, in the groups `docsGroups` names. A page the data holds but no
 *  group lists still appears, under its own group, so nothing written goes
 *  missing from the list. */
export function docsNav(): DocsNavGroup[] {
    const seen = new Set<string>();
    const groups: DocsNavGroup[] = docsGroups.map((group) => ({
        title: group.title,
        pages: group.pages.flatMap((slug) => {
            const page = docsPageMap.get(slug);
            if (!page || seen.has(slug)) return [];
            seen.add(slug);
            return [{ slug, title: page.title }];
        }),
    }));
    for (const page of docsPages) {
        if (seen.has(page.slug)) continue;
        seen.add(page.slug);
        let group = groups.find((g) => g.title === page.group);
        if (!group) {
            group = { title: page.group, pages: [] };
            groups.push(group);
        }
        group.pages.push({ slug: page.slug, title: page.title });
    }
    return groups.filter((group) => group.pages.length > 0);
}

/** Every page, in the order the guide list shows them. */
export function docsInOrder(): DocsPage[] {
    return docsNav().flatMap((group) =>
        group.pages.flatMap((p) => docsPageMap.get(p.slug) ?? []),
    );
}

/** The page before and after, in the order the guide list shows them. */
export function docsNeighbours(slug: string): {
    previous: DocsPage | null;
    next: DocsPage | null;
} {
    const order = docsInOrder();
    const at = order.findIndex((page) => page.slug === slug);
    if (at === -1) return { previous: null, next: null };
    return { previous: order[at - 1] ?? null, next: order[at + 1] ?? null };
}

export type DocsCodeBlock = Extract<DocsBlock, { type: "code" }>;

/** One thing on a page: a block as written, or consecutive code blocks that
 *  are the same step in different languages, shown as tabs. */
export type DocsPiece =
    | { kind: "block"; block: DocsBlock; id: string | null }
    | {
          kind: "tabs";
          title: string;
          body?: string;
          id: string;
          tabs: DocsCodeBlock[];
      };

/** "Install (Python)" and "Install (TypeScript)" are one step. */
const LANGUAGE_NOTE =
    /\s*\((?:typescript|javascript|python|react|tsx|ts|js|py|shell|bash)\)\s*$/i;

function stepOf(block: DocsCodeBlock): string {
    return block.title.replace(LANGUAGE_NOTE, "").trim().toLowerCase();
}

/** A page cut into what it shows. Ids are the ones its headings have always
 *  had: GitHub's slugs of the titled blocks, callouts aside, in order. Two
 *  code blocks become tabs only when the data says they are one step -- the
 *  same title, a different language each, and nothing new said above the
 *  second. */
export function docsPieces(page: DocsPage): DocsPiece[] {
    const slugger = new GithubSlugger();
    const pieces: DocsPiece[] = [];
    for (const block of page.blocks) {
        const last = pieces.at(-1);
        if (block.type === "code" && last) {
            const prior =
                last.kind === "tabs"
                    ? last.tabs
                    : last.block.type === "code"
                      ? [last.block]
                      : null;
            if (
                prior &&
                last.id &&
                stepOf(prior[0]) === stepOf(block) &&
                prior.every((b) => b.language !== block.language) &&
                (!block.body || block.body === prior[0].body)
            ) {
                if (last.kind === "tabs") last.tabs.push(block);
                else
                    pieces[pieces.length - 1] = {
                        kind: "tabs",
                        title: prior[0].title.replace(LANGUAGE_NOTE, ""),
                        body: prior[0].body,
                        id: last.id,
                        tabs: [prior[0], block],
                    };
                continue;
            }
        }
        const id =
            block.type !== "callout" && block.title
                ? slugger.slug(block.title)
                : null;
        pieces.push({ kind: "block", block, id });
    }
    return pieces;
}

export type DocsSection = { id: string; title: string };

/** The page's h2s, for "On this page" and for search. */
export function docsSections(page: DocsPage): DocsSection[] {
    return docsPieces(page).flatMap((piece) => {
        if (piece.kind === "tabs") return [{ id: piece.id, title: piece.title }];
        return piece.id && piece.block.title
            ? [{ id: piece.id, title: piece.block.title }]
            : [];
    });
}

const SHELLS = new Set(["bash", "sh", "shell", "zsh", "console", "terminal"]);

export function isShell(language: string): boolean {
    return SHELLS.has(language.toLowerCase());
}

const LABELS: Record<string, string> = {
    ts: "TypeScript",
    typescript: "TypeScript",
    tsx: "TSX",
    js: "JavaScript",
    javascript: "JavaScript",
    jsx: "JSX",
    python: "Python",
    py: "Python",
    html: "HTML",
    css: "CSS",
    json: "JSON",
    yaml: "YAML",
    sql: "SQL",
};

/** What the bar over a code block says it holds. */
export function codeLabel(language: string): string {
    if (isShell(language)) return "Terminal";
    return LABELS[language.toLowerCase()] ?? language.toUpperCase();
}

export type ShellLine = {
    text: string;
    /** `command` takes a `$ ` prompt; `more` carries on the one above. */
    kind: "command" | "more" | "comment" | "blank";
};

/** Which quote is still open at the end of `text`, given the one open at its
 *  start. A comment ends the reading. */
function openQuote(text: string, open: string | null): string | null {
    let quote = open;
    for (let i = 0; i < text.length; i++) {
        const c = text[i];
        if (quote) {
            if (c === quote) quote = null;
            else if (c === "\\" && quote === '"') i++;
        } else if (c === "'" || c === '"') quote = c;
        else if (c === "\\") i++;
        else if (c === "#" && (i === 0 || /\s/.test(text[i - 1]))) break;
    }
    return quote;
}

/** A shell block, read for its prompts: each command starts a `$ `, and a
 *  command carried on by a trailing backslash or a quote left open (a JSON
 *  payload over several rows) does not get another. */
export function shellLines(code: string): ShellLine[] {
    const lines: ShellLine[] = [];
    let quote: string | null = null;
    let carried = false;
    for (const text of code.split("\n")) {
        let kind: ShellLine["kind"];
        if (quote !== null || carried) kind = "more";
        else if (!text.trim()) kind = "blank";
        else if (text.trimStart().startsWith("#")) kind = "comment";
        else kind = "command";
        lines.push({ text, kind });
        if (kind === "blank" || kind === "comment") continue;
        quote = openQuote(text, quote);
        carried = quote === null && /\\\s*$/.test(text);
    }
    return lines;
}

/** The commands in a shell block, one string each, spaces collapsed. */
export function shellCommands(code: string): string[] {
    const commands: string[] = [];
    for (const line of shellLines(code)) {
        if (line.kind === "command") commands.push(line.text);
        else if (line.kind === "more" && commands.length)
            commands[commands.length - 1] += " " + line.text;
    }
    return commands.map((c) => c.replace(/\s+/g, " ").trim());
}

/** Every code block a piece holds. */
export function pieceCode(piece: DocsPiece): DocsCodeBlock[] {
    if (piece.kind === "tabs") return piece.tabs;
    return piece.block.type === "code" ? [piece.block] : [];
}

const SOURCE_DIR = "lemma-frontend/src/site/data/";

/** The file on GitHub that holds a page's words. */
export function docsSourceUrl(page: DocsPage): string {
    const file = educationGuidePages.includes(page)
        ? "education-guides.ts"
        : conceptDocsPages.includes(page) ||
            page === howLemmaWorksStub ||
            page === glossaryStub
          ? "concept-docs.ts"
          : "docs.ts";
    return `${githubUrl}/blob/main/${SOURCE_DIR}${file}`;
}

export type StartStep = { title: string; command: string; href: string };

/** The docs home's three first steps, with the commands the Quickstart page
 *  itself gives. A step whose command the page stops giving is dropped
 *  rather than shown out of date. */
export function quickstartSteps(): StartStep[] {
    const slug = "getting-started";
    const page = docsPageMap.get(slug);
    if (!page) return [];
    const pieces = docsPieces(page);
    const find = (test: RegExp) => {
        for (const piece of pieces) {
            for (const block of pieceCode(piece)) {
                if (!isShell(block.language)) continue;
                const command = shellCommands(block.code).find((c) =>
                    test.test(c),
                );
                if (command)
                    return {
                        command,
                        href: `/docs/${slug}${piece.id ? "#" + piece.id : ""}`,
                    };
            }
        }
        return null;
    };
    const steps: [string, RegExp][] = [
        ["Install the CLI", /^uv tool install lemma-terminal\b/],
        ["Sign in", /^lemma auth login\b/],
        ["Make a starter", /\s--with-starter\b/],
    ];
    return steps.flatMap(([title, test]) => {
        const found = find(test);
        return found ? [{ title, ...found }] : [];
    });
}

/** `cd` moves the reader's shell; it is not a thing anybody searches for. */
const NOT_A_COMMAND = /^cd\s/;

/** Every page, its sections and its commands, in the order the pages are
 *  given. A command that appears on more than one page is listed once, where
 *  it appears first. */
export function buildDocsSearchIndex(pages: readonly DocsPage[]): DocsHit[] {
    const hits: DocsHit[] = [];
    const commands = new Set<string>();
    for (const page of pages) {
        const href = "/docs/" + page.slug;
        hits.push({ kind: "page", title: page.title, where: page.group, href });
        for (const piece of docsPieces(page)) {
            const title =
                piece.kind === "tabs" ? piece.title : piece.block.title;
            if (!piece.id || !title) continue;
            const at = href + "#" + piece.id;
            hits.push({ kind: "section", title, where: page.title, href: at });
            for (const block of pieceCode(piece)) {
                if (!isShell(block.language)) continue;
                for (const command of shellCommands(block.code)) {
                    if (NOT_A_COMMAND.test(command) || commands.has(command))
                        continue;
                    commands.add(command);
                    hits.push({
                        kind: "command",
                        title: command,
                        where: page.title + " · " + title,
                        href: at,
                    });
                }
            }
        }
    }
    return hits;
}

