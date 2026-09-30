/** Comments on a page, as data.
 *
 *  There is no comments feature on the server, so a comment is a row in one
 *  pod table (`doc_comments`) — which is also what lets an agent be woken by
 *  one: a DATASTORE schedule fires on the insert. The row has to carry
 *  everything the schedule and the agent need, because the event is the row:
 *  which file, which passage, what was said, and which bot was named.
 *
 *  A comment is anchored to the words it is about, not to a position. The
 *  file is markdown that agents rewrite freely, so a character offset would
 *  point somewhere else after the first edit; the quoted text, with a little
 *  of what came before and after it, is found again wherever it moved.
 */

export const COMMENTS_TABLE = "doc_comments";

export interface CommentRow {
    id: string;
    filePath: string;
    /** The passage it is about; empty for a comment on the page as a whole. */
    quote: string;
    quotePrefix: string;
    quoteSuffix: string;
    body: string;
    /** The thread it belongs to; null for the comment that started one. */
    parentId: string | null;
    resolved: boolean;
    /** Who wrote it: a person (by user id), or a bot that replied. */
    authorId: string | null;
    /** Their name as it was when they wrote it. */
    authorName: string | null;
    authorAgent: string | null;
    /** The bot a schedule wakes for this comment, by wire name. */
    mentionedAgent: string | null;
    createdAt: string | null;
}

export interface Thread {
    root: CommentRow;
    replies: CommentRow[];
}

function text(value: unknown): string {
    return typeof value === "string" ? value : "";
}

export function readComment(raw: unknown): CommentRow | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    const id = text(row.id) || (typeof row.id === "number" ? String(row.id) : "");
    const filePath = text(row.file_path);
    if (!id || !filePath) return null;
    return {
        id,
        filePath,
        quote: text(row.quote),
        quotePrefix: text(row.quote_prefix),
        quoteSuffix: text(row.quote_suffix),
        body: text(row.body),
        parentId: text(row.parent_id) || null,
        resolved: row.resolved === true,
        authorId: text(row.written_by) || null,
        authorName: text(row.author_name) || null,
        authorAgent: text(row.author_agent) || null,
        mentionedAgent: text(row.mentioned_agent) || null,
        createdAt: text(row.created_at) || null,
    };
}

/** Threads in reading order: by where they start, oldest first; replies by time. */
export function threadsOf(rows: CommentRow[]): Thread[] {
    const byTime = [...rows].sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? ""));
    const roots = byTime.filter((row) => !row.parentId);
    const known = new Set(roots.map((row) => row.id));
    const replies = new Map<string, CommentRow[]>();
    for (const row of byTime) {
        if (!row.parentId) continue;
        /* A reply whose root is gone still shows, as a thread of its own. */
        if (!known.has(row.parentId)) { roots.push(row); known.add(row.id); continue; }
        replies.set(row.parentId, [...(replies.get(row.parentId) ?? []), row]);
    }
    return roots.map((root) => ({ root, replies: replies.get(root.id) ?? [] }));
}

/* ── anchoring ─────────────────────────────────────────────────────── */

const CONTEXT = 32;
const MAX_QUOTE = 600;

/** What to store about a selection: the words, and a little either side. */
export function anchorOf(whole: string, from: number, to: number): { quote: string; quotePrefix: string; quoteSuffix: string } {
    const quote = whole.slice(from, to).slice(0, MAX_QUOTE);
    return {
        quote,
        quotePrefix: whole.slice(Math.max(0, from - CONTEXT), from),
        quoteSuffix: whole.slice(to, to + CONTEXT),
    };
}

/** Where the quoted words are now, in `whole`. The occurrence whose
 *  surroundings match best wins, so the same sentence twice in a page lands
 *  on the right one; null when the words are gone. */
export function findAnchor(whole: string, anchor: { quote: string; quotePrefix: string; quoteSuffix: string }): { from: number; to: number } | null {
    const { quote } = anchor;
    if (!quote.trim()) return null;
    let best: { from: number; score: number } | null = null;
    for (let at = whole.indexOf(quote); at !== -1; at = whole.indexOf(quote, at + 1)) {
        const before = whole.slice(Math.max(0, at - anchor.quotePrefix.length), at);
        const after = whole.slice(at + quote.length, at + quote.length + anchor.quoteSuffix.length);
        const score = shared(before, anchor.quotePrefix, true) + shared(after, anchor.quoteSuffix, false);
        if (!best || score > best.score) best = { from: at, score };
    }
    return best ? { from: best.from, to: best.from + quote.length } : null;
}

/** How many characters two strings share at the end (`fromEnd`) or start. */
function shared(a: string, b: string, fromEnd: boolean): number {
    let count = 0;
    const length = Math.min(a.length, b.length);
    for (let i = 1; i <= length; i++) {
        const left = fromEnd ? a[a.length - i] : a[i - 1];
        const right = fromEnd ? b[b.length - i] : b[i - 1];
        if (left !== right) break;
        count++;
    }
    return count;
}

/* ── mentions ──────────────────────────────────────────────────────── */

export interface Mentionable {
    kind: "agent" | "person";
    /** What is typed after the @, and shown. */
    label: string;
    /** The agent's wire name, or the person's user id. */
    key: string;
}

/** Who a comment names. Longest label first, so "@Deal desk" is not read as
 *  "@Deal"; matched case-insensitively and only at a word boundary. */
export function mentionsIn(body: string, known: Mentionable[]): Mentionable[] {
    const ordered = [...known].sort((a, b) => b.label.length - a.label.length);
    const found: Mentionable[] = [];
    const lower = body.toLowerCase();
    for (let at = lower.indexOf("@"); at !== -1; at = lower.indexOf("@", at + 1)) {
        if (at > 0 && /[\w]/.test(lower[at - 1])) continue;
        const rest = lower.slice(at + 1);
        const hit = ordered.find((one) => rest.startsWith(one.label.toLowerCase()) && !/[\w]/.test(rest[one.label.length] ?? ""));
        if (hit && !found.some((one) => one.kind === hit.kind && one.key === hit.key)) found.push(hit);
    }
    return found;
}

/** The half-typed mention at the caret, for the suggestion list. */
export function mentionAt(body: string, caret: number): { start: number; query: string } | null {
    const before = body.slice(0, caret);
    const match = /(^|[\s(])@([\w .-]{0,40})$/.exec(before);
    if (!match) return null;
    const query = match[2];
    if (/\s{2}|^\s/.test(query)) return null;
    return { start: caret - query.length - 1, query };
}

/** The row to insert. The first bot named is the one woken: a schedule's
 *  condition can only test one column for one value, so one comment wakes one
 *  bot, and the composer says which. */
export function commentRow(input: {
    filePath: string;
    anchor?: { quote: string; quotePrefix: string; quoteSuffix: string } | null;
    body: string;
    parentId?: string | null;
    mentions: Mentionable[];
}): Record<string, unknown> {
    const agent = input.mentions.find((one) => one.kind === "agent") ?? null;
    return {
        file_path: input.filePath,
        quote: input.anchor?.quote ?? "",
        quote_prefix: input.anchor?.quotePrefix ?? "",
        quote_suffix: input.anchor?.quoteSuffix ?? "",
        body: input.body.trim(),
        parent_id: input.parentId ?? null,
        resolved: false,
        mentioned_agent: agent ? agent.key : null,
    };
}

/** The ask in a thread still waiting on a bot: the latest comment that named
 *  one, with no bot reply after it. Null when every ask was answered. */
export function pendingAsk(thread: Thread): CommentRow | null {
    const all = [thread.root, ...thread.replies];
    for (let at = all.length - 1; at >= 0; at--) {
        const row = all[at];
        if (row.authorAgent) return null;
        if (row.mentionedAgent) return row;
    }
    return null;
}
