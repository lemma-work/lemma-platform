/** What a review suggests, and what one tap on Add does with it.
 *
 *  The review writes suggestions as rows of `review_suggestions`; it never
 *  makes the change itself. A person adds one or puts it away. Adding is done
 *  here, in the app, as that person: a memory note written, a skill file
 *  rewritten, or a schedule created — the three changes a review may propose.
 *
 *  Everything a row says is checked before it is acted on, because the row was
 *  written by a model: a path outside the folder it belongs to, a skill whose
 *  frontmatter the loader would refuse, or a cron the platform would reject is
 *  turned into a reason instead of a write. */

import { readFrontmatter } from "@/skills/skill-frontmatter";

export const SUGGESTIONS_TABLE = "review_suggestions";

export type SuggestionKind = "remember" | "skill" | "standing_work";
export type SuggestionStatus = "open" | "added" | "dismissed";

export interface Suggestion {
    id: string;
    week: string;
    kind: SuggestionKind;
    title: string;
    why: string;
    path: string;
    content: string;
    cron: string;
    status: SuggestionStatus;
}

const KINDS: readonly SuggestionKind[] = ["remember", "skill", "standing_work"];
const STATUSES: readonly SuggestionStatus[] = ["open", "added", "dismissed"];

function text(value: unknown): string {
    return typeof value === "string" ? value : "";
}

export function readSuggestion(row: unknown): Suggestion | null {
    if (!row || typeof row !== "object") return null;
    const raw = row as Record<string, unknown>;
    const id = text(raw.id);
    const title = text(raw.title).trim();
    if (!id || !title || !KINDS.includes(raw.kind as SuggestionKind)) return null;
    return {
        id,
        week: text(raw.week).slice(0, 10),
        kind: raw.kind as SuggestionKind,
        title,
        why: text(raw.why).trim(),
        path: text(raw.path).trim(),
        content: text(raw.content),
        cron: text(raw.cron).trim(),
        status: STATUSES.includes(raw.status as SuggestionStatus) ? (raw.status as SuggestionStatus) : "open",
    };
}

/** The table, made by the app when the review is turned on. The review only
 *  ever adds rows to it. */
export const SUGGESTION_COLUMNS = [
    { name: "week", type: "DATE", required: false },
    { name: "kind", type: "TEXT", required: true },
    { name: "title", type: "TEXT", required: true },
    { name: "why", type: "TEXT", required: false },
    { name: "path", type: "TEXT", required: false },
    { name: "content", type: "TEXT", required: false },
    { name: "cron", type: "TEXT", required: false },
    { name: "status", type: "TEXT", required: true, default: "open" },
] as const;

/** One tap's worth of work, decided before anything is written. */
export type Plan =
    | { kind: "write"; path: string; text: string }
    | { kind: "schedule"; name: string; cron: string; instruction: string }
    | { kind: "refuse"; reason: string };

/** Memory notes the app may write: one file directly under /memory, never
 *  the index, which the teammate keeps. */
const MEMORY_PATH = /^\/memory\/([a-z0-9][a-z0-9-]{0,62})\.md$/;
const SKILL_PATH = /^\/skills\/([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)\/SKILL\.md$/;

/** The longest note or instruction a suggestion may carry. Long enough for
 *  any real note; short enough that a runaway row is refused, not saved. */
const MOST = 8000;

/** What Add does with `suggestion`, given the file at its path today (null
 *  when there is none). */
export function planFor(suggestion: Suggestion, existing: string | null): Plan {
    const content = suggestion.content.trim();
    if (!content) return { kind: "refuse", reason: "The suggestion came without its text." };
    if (content.length > MOST) return { kind: "refuse", reason: "The suggestion is too long to add as it is." };

    if (suggestion.kind === "remember") {
        const match = MEMORY_PATH.exec(suggestion.path);
        if (!match || match[1] === "agents") return { kind: "refuse", reason: "It names a note outside the memory folder." };
        /* A note that already exists is added to, not replaced: the teammate
           wrote it, and a review should not erase what it knew. */
        const text = existing && existing.trim() ? existing.trimEnd() + "\n\n" + content + "\n" : content + "\n";
        return { kind: "write", path: suggestion.path, text };
    }

    if (suggestion.kind === "skill") {
        const match = SKILL_PATH.exec(suggestion.path);
        if (!match) return { kind: "refuse", reason: "It names a file that is not a skill." };
        if (existing === null) return { kind: "refuse", reason: "That skill is not here any more." };
        const front = readFrontmatter(content + "\n", match[1]);
        if (front.problem) return { kind: "refuse", reason: "The new version would not load: " + front.problem };
        return { kind: "write", path: suggestion.path, text: content + "\n" };
    }

    const fields = suggestion.cron.split(/\s+/).filter(Boolean);
    if (fields.length !== 5) return { kind: "refuse", reason: "It has no schedule the platform can run." };
    if (/^\*(\/\d+)?$/.test(fields[0]) && !/^\*\/(1[5-9]|[2-5]\d)$/.test(fields[0])) {
        return { kind: "refuse", reason: "It would run more often than the platform allows." };
    }
    return { kind: "schedule", name: scheduleName(suggestion.title), cron: fields.join(" "), instruction: content };
}

/** A schedule's name from the suggestion's title: lowercase words joined by
 *  underscores, the shape schedule names already take. */
export function scheduleName(title: string): string {
    const words = title.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, " ").trim().split(" ").filter(Boolean);
    return (words.join("_") || "suggested").slice(0, 60);
}

/** What a kind of suggestion changes, in a word for its row. */
export function kindWord(kind: SuggestionKind): string {
    return kind === "remember" ? "Remember" : kind === "skill" ? "Skill" : "Standing work";
}

/** What the Add button says. */
export function addWord(kind: SuggestionKind): string {
    return kind === "remember" ? "Keep note" : kind === "skill" ? "Update skill" : "Turn on";
}
