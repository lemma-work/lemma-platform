/* The docs' search, run in the browser as somebody types: page titles,
   their section titles, and the commands in their shell blocks. The index is
   built from the pages when the site is built (`buildDocsSearchIndex` in
   `docs-content.ts`); this file holds no docs of its own, so the browser is
   sent the index and not every page. */

export type DocsHitKind = "page" | "section" | "command";

export type DocsHit = {
    kind: DocsHitKind;
    title: string;
    /** Where it is: a page's group, a section's page, a command's section. */
    where: string;
    href: string;
};

export type DocsResultGroup = {
    kind: DocsHitKind;
    label: string;
    hits: DocsHit[];
};

const ORDER: DocsHitKind[] = ["page", "section", "command"];
const LABELS: Record<DocsHitKind, string> = {
    page: "Pages",
    section: "Sections",
    command: "Commands",
};
const LIMITS: Record<DocsHitKind, number> = { page: 6, section: 8, command: 6 };

function words(query: string): string[] {
    return [...new Set(query.toLowerCase().split(/\s+/).filter(Boolean))];
}

/** What matches every word of the query, in a title or in where it is,
 *  grouped by kind. Inside a group a title that is the query comes first,
 *  then one that starts with it, holds it, holds every word, and last a hit
 *  that matched only where it is; ties keep the docs' own order. */
export function searchDocs(
    index: readonly DocsHit[],
    query: string,
): DocsResultGroup[] {
    const terms = words(query);
    if (terms.length === 0) return [];
    const phrase = query.trim().toLowerCase().replace(/\s+/g, " ");
    const scored: { hit: DocsHit; score: number; order: number }[] = [];
    index.forEach((hit, order) => {
        const title = hit.title.toLowerCase();
        const where = hit.where.toLowerCase();
        if (!terms.every((t) => title.includes(t) || where.includes(t)))
            return;
        const score =
            title === phrase
                ? 0
                : title.startsWith(phrase)
                  ? 1
                  : title.includes(phrase)
                    ? 2
                    : terms.every((t) => title.includes(t))
                      ? 3
                      : 4;
        scored.push({ hit, score, order });
    });
    scored.sort((a, b) => a.score - b.score || a.order - b.order);
    return ORDER.map((kind) => ({
        kind,
        label: LABELS[kind],
        hits: scored
            .filter((s) => s.hit.kind === kind)
            .slice(0, LIMITS[kind])
            .map((s) => s.hit),
    })).filter((group) => group.hits.length > 0);
}

const SUGGESTED = [
    "/docs/getting-started",
    "/docs/overview",
    "/docs/sdk/installation",
    "/docs/sdk/client",
    "/docs/cli/overview",
    "/docs/guides/build-a-app",
];

/** Where to start, before anything is typed. */
export function suggestedDocs(index: readonly DocsHit[]): DocsResultGroup[] {
    const hits = SUGGESTED.flatMap((href) =>
        index.filter((hit) => hit.kind === "page" && hit.href === href),
    );
    return hits.length ? [{ kind: "page", label: "Suggested", hits }] : [];
}

export type MarkedText = { text: string; match: boolean }[];

/** `text` cut where the query's words match it, for marking. */
export function markMatches(text: string, query: string): MarkedText {
    const terms = words(query);
    if (!text || terms.length === 0) return [{ text, match: false }];
    const lower = text.toLowerCase();
    const marked = new Array<boolean>(text.length).fill(false);
    for (const term of terms) {
        for (let at = lower.indexOf(term); at !== -1; at = lower.indexOf(term, at + term.length)) {
            marked.fill(true, at, at + term.length);
        }
    }
    const pieces: MarkedText = [];
    let start = 0;
    for (let i = 1; i <= text.length; i++) {
        if (i === text.length || marked[i] !== marked[start]) {
            pieces.push({ text: text.slice(start, i), match: marked[start] });
            start = i;
        }
    }
    return pieces;
}
