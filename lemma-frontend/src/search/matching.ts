/** What "matches" means, and which match is better than which.
 *
 *  Its own module because ranking is the whole of whether a search box is worth
 *  having. A box that finds the right thing and puts it fourth is a box people
 *  stop using — and the difference between fourth and first is entirely in
 *  here, where it can be tested without a network or a browser.
 */

/** The kinds a result can be. Order matters: it is the tiebreak when two
 *  things match equally well, and it is roughly "how likely were you looking
 *  for this" — you search for a teammate by name far more often than you search
 *  for the table a record happens to live in. */
export const KINDS = [
    "teammate",
    "conversation",
    "doc",
    "record",
    "app",
    "agent",
    "workflow",
    "function",
    "table",
    "person",
    "schedule",
] as const;

export type SearchKind = (typeof KINDS)[number];

export interface Candidate {
    kind: SearchKind;
    id: string;
    title: string;
    /** Shown under the title — a path, a table name, who it belongs to. */
    subtitle?: string | null;
    /** Extra text that should match but is not shown, such as a file path. */
    haystack?: string;
    /** Carried through untouched so the caller knows what to open. */
    payload?: unknown;
}

export interface Hit extends Candidate {
    score: number;
    /** Character ranges in `title` that matched, for highlighting. */
    ranges: [number, number][];
}

/* Scores are bands rather than a continuous scale, so a better *kind* of match
   always beats a worse one no matter how long the strings are. Within a band,
   an earlier and tighter match wins. */
const EXACT = 1000;
const PREFIX = 800;
const WORD = 600;
const CONTAINS = 400;
const SUBSEQUENCE = 200;
const ELSEWHERE = 60; // matched the hidden haystack rather than the title

/** Where each word of the title starts — used for "matches the start of a
 *  word", which is what people mean when they type `wf` for "weekly flow". */
function wordStarts(text: string): number[] {
    const starts: number[] = [];
    for (let index = 0; index < text.length; index += 1) {
        const before = index === 0 ? " " : text[index - 1];
        if (/[\s\-_/.:]/.test(before)) starts.push(index);
        /* camelCase and PascalCase are word boundaries too: `podBrief` should
           be findable as `pb`. */
        else if (index > 0 && text[index] >= "A" && text[index] <= "Z" && text[index - 1] !== text[index - 1].toUpperCase()) {
            starts.push(index);
        }
    }
    return starts;
}

/** Every character of `query`, in order, somewhere in `text`.
 *
 *  The loosest thing that still counts. Returns the positions so a fuzzy match
 *  can still be highlighted honestly rather than underlining the whole string.
 */
function subsequenceOf(text: string, query: string): number[] | null {
    const at: number[] = [];
    let cursor = 0;
    for (const character of query) {
        const found = text.indexOf(character, cursor);
        if (found === -1) return null;
        at.push(found);
        cursor = found + 1;
    }
    return at;
}

function toRanges(positions: number[]): [number, number][] {
    const ranges: [number, number][] = [];
    for (const position of positions) {
        const last = ranges[ranges.length - 1];
        if (last && last[1] === position) last[1] = position + 1;
        else ranges.push([position, position + 1]);
    }
    return ranges;
}

/** How well one candidate answers one query, or null for not at all. */
export function scoreOne(candidate: Candidate, rawQuery: string): Hit | null {
    const query = rawQuery.trim().toLowerCase();
    if (!query) return null;

    const title = candidate.title ?? "";
    const lower = title.toLowerCase();

    if (lower === query) {
        return { ...candidate, score: EXACT, ranges: [[0, title.length]] };
    }
    if (lower.startsWith(query)) {
        return { ...candidate, score: PREFIX - query.length, ranges: [[0, query.length]] };
    }
    for (const start of wordStarts(title)) {
        if (lower.startsWith(query, start)) {
            /* Earlier words beat later ones: "review" should find "Review
               notes" above "Design review". */
            return { ...candidate, score: WORD - start, ranges: [[start, start + query.length]] };
        }
    }
    const contains = lower.indexOf(query);
    if (contains !== -1) {
        return { ...candidate, score: CONTAINS - contains, ranges: [[contains, contains + query.length]] };
    }

    /* Only for queries short enough that a subsequence means something. Every
       long query matches something as a subsequence, and those matches are
       noise wearing the shape of a result. */
    if (query.length >= 2 && query.length <= 12) {
        const positions = subsequenceOf(lower, query);
        if (positions) {
            /* Tighter is better: the span the match covers, not the string it
               was found in — `abc` inside `abcdefghij` beats `a...b...c`. */
            const span = positions[positions.length - 1] - positions[0];
            return { ...candidate, score: SUBSEQUENCE - span, ranges: toRanges(positions) };
        }
    }

    /* The title says nothing, but a path or a table name might. Ranked below
       everything that matched what is actually on screen, because a result
       whose visible text does not contain what you typed looks like a mistake
       until you read the second line. */
    const elsewhere = (candidate.subtitle ?? "") + " " + (candidate.haystack ?? "");
    if (elsewhere.toLowerCase().includes(query)) {
        return { ...candidate, score: ELSEWHERE, ranges: [] };
    }
    return null;
}

/** Rank everything, best first.
 *
 *  Ties break on kind order and then on title, so the same query always
 *  produces the same list — a search box whose results reshuffle between
 *  identical keystrokes is one nobody can build a habit on.
 */
export function rank(candidates: readonly Candidate[], query: string, limit = 40): Hit[] {
    const hits: Hit[] = [];
    for (const candidate of candidates) {
        const hit = scoreOne(candidate, query);
        if (hit) hits.push(hit);
    }
    hits.sort((a, b) =>
        b.score - a.score ||
        KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind) ||
        a.title.localeCompare(b.title) ||
        a.id.localeCompare(b.id),
    );
    return hits.slice(0, limit);
}

/** Split a title into matched and unmatched pieces, for rendering.
 *
 *  Returns pieces rather than HTML: the caller decides what emphasis looks
 *  like, and nothing here has to know about the DOM or trust a string.
 */
export function highlight(title: string, ranges: readonly [number, number][]): { text: string; hit: boolean }[] {
    if (ranges.length === 0) return [{ text: title, hit: false }];
    const pieces: { text: string; hit: boolean }[] = [];
    let cursor = 0;
    for (const [start, end] of ranges) {
        if (start > cursor) pieces.push({ text: title.slice(cursor, start), hit: false });
        pieces.push({ text: title.slice(start, end), hit: true });
        cursor = end;
    }
    if (cursor < title.length) pieces.push({ text: title.slice(cursor), hit: false });
    return pieces;
}

/** What each kind is called in the list. */
export const KIND_LABEL: Record<SearchKind, string> = {
    teammate: "Teammate",
    conversation: "Conversation",
    doc: "Document",
    record: "Record",
    app: "App",
    agent: "Agent",
    workflow: "Workflow",
    function: "Function",
    table: "Table",
    person: "Person",
    schedule: "Schedule",
};

/** The ranked list, gathered under one heading per kind.
 *
 *  Grouping by walking the ranked list and starting a new group whenever the
 *  kind changes is the obvious way and the wrong one: ranking interleaves kinds
 *  by score, so "Teammate" turns up three times and "Conversation" four,
 *  scattered down the list. Observed, not imagined — it is what the first live
 *  search produced.
 *
 *  So: one group per kind, groups ordered by their best hit, hits kept in rank
 *  order inside. The best match in the list is still in the first group, which
 *  is what stops grouping from fighting the ranking it is displaying.
 */
export interface HitGroup {
    kind: SearchKind;
    /** The heading. A kind's name, except for records, where the kind is not
     *  the useful thing to say. */
    label: string;
    hits: Hit[];
}

/** What a group of hits is called, and what separates one group from the next.
 *
 *  For everything but a record the kind is the answer. A record's kind is the
 *  least interesting thing about it — "RECORD" over five rows each captioned
 *  `connections` says the same word twice and the useful word never. The table
 *  is the heading, and rows from two tables are two groups.
 */
function groupingOf(hit: Hit): { key: string; label: string } {
    if (hit.kind === "record") {
        const table = (hit.subtitle ?? "").trim();
        if (table) return { key: "record:" + table, label: table };
    }
    return { key: hit.kind, label: KIND_LABEL[hit.kind] };
}

export function groupByKind(hits: readonly Hit[]): HitGroup[] {
    const groups = new Map<string, HitGroup>();
    for (const hit of hits) {
        const { key, label } = groupingOf(hit);
        const existing = groups.get(key);
        if (existing) existing.hits.push(hit);
        else groups.set(key, { kind: hit.kind, label, hits: [hit] });
    }
    /* Insertion order is already best-first: the map was filled by walking the
       ranked list, so a group is inserted when its best hit is reached. */
    return [...groups.values()];
}
