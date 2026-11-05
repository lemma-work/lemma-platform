import type { Mentionable } from "./mentions";

/** What somebody was writing in a channel, or in one thread of it, kept
 *  while they look elsewhere: switching threads, closing the panel, opening
 *  another channel and coming back. One draft per place it would be sent --
 *  the channel itself, or each thread by its first line -- so a reply begun
 *  in one thread never turns up in another.
 *
 *  A draft keeps the people picked from the `@` menu beside the words: only
 *  the menu knows which "@Sam" was meant, so words kept without them would
 *  come back naming nobody.
 *
 *  Held for the tab (`sessionStorage`), and in memory where storage is
 *  refused; a draft is a convenience, never something worth an error. */

export interface ChannelDraft {
    text: string;
    picked: Mentionable[];
}

const PREFIX = "lemma.channel-draft:";
const EMPTY: ChannelDraft = { text: "", picked: [] };
const memory = new Map<string, ChannelDraft>();

export function draftKey(podId: string, channelId: string, threadRootId?: string | null): string {
    return PREFIX + podId + ":" + channelId + ":" + (threadRootId ?? "");
}

function storage(): Storage | null {
    try {
        return typeof sessionStorage === "undefined" ? null : sessionStorage;
    } catch {
        return null;
    }
}

const KINDS = new Set(["MEMBER", "CONTACT", "AGENT"]);

function readPicked(raw: unknown): Mentionable[] {
    if (!Array.isArray(raw)) return [];
    return raw.flatMap((entry): Mentionable[] => {
        const row = entry && typeof entry === "object" ? (entry as Record<string, unknown>) : {};
        if (!KINDS.has(String(row.kind)) || typeof row.id !== "string" || typeof row.name !== "string") return [];
        return [{
            kind: row.kind as Mentionable["kind"],
            id: row.id,
            name: row.name,
            ...(typeof row.inChannel === "boolean" ? { inChannel: row.inChannel } : {}),
        }];
    });
}

/** A stored draft: what this build writes, or a bare string from the one
 *  before it, which kept the words alone. */
export function parseDraft(stored: string | null): ChannelDraft {
    if (!stored) return EMPTY;
    try {
        const parsed: unknown = JSON.parse(stored);
        if (parsed && typeof parsed === "object" && typeof (parsed as { text?: unknown }).text === "string") {
            const row = parsed as { text: string; picked?: unknown };
            return { text: row.text, picked: readPicked(row.picked) };
        }
    } catch {
        /* the words alone, as an earlier build kept them */
    }
    return { text: stored, picked: [] };
}

export function readDraft(key: string): ChannelDraft {
    const held = memory.get(key);
    if (held !== undefined) return held;
    try {
        return parseDraft(storage()?.getItem(key) ?? null);
    } catch {
        return EMPTY;
    }
}

/** Keep `draft`; one with no words is forgotten, and so are its picks. */
export function keepDraft(key: string, draft: ChannelDraft): void {
    if (draft.text) memory.set(key, draft);
    else memory.delete(key);
    try {
        if (draft.text) storage()?.setItem(key, JSON.stringify(draft));
        else storage()?.removeItem(key);
    } catch {
        /* storage refused: the in-memory copy is enough for this tab */
    }
}
