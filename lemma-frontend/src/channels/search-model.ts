import { readLine, readPerson, type ChannelKind, type Line, type Page } from "./model";
import type { PersonRef } from "./waiting";

/** One line found by `GET /search`, in the channel it was said in -- only
 *  channels the reader may see, which the server decides. */
export interface SearchHit {
    line: Line;
    channel: { id: string; kind: ChannelKind; title: string | null; person: PersonRef | null };
    /** The server's plain-text cut of the line around the match, when it
     *  made one: the words searched for are marked here, not by the server. */
    snippet: string | null;
}

type Wire = Record<string, unknown>;
const record = (value: unknown): Wire => (value && typeof value === "object" ? (value as Wire) : {});

export function readSearchPage(raw: unknown): Page<SearchHit> {
    const body = record(raw);
    const items = Array.isArray(body.items) ? body.items : [];
    return {
        items: items.flatMap((entry): SearchHit[] => {
            const row = record(entry);
            const line = readLine(row.message);
            const channel = record(row.channel);
            if (!line || typeof channel.id !== "string" || !channel.id) return [];
            const kind = channel.kind === "DIRECT" || channel.kind === "CONTACT" ? channel.kind : "GROUP";
            return [{
                line,
                channel: {
                    id: channel.id, kind,
                    title: typeof channel.title === "string" && channel.title ? channel.title : null,
                    person: readPerson(channel.person),
                },
                snippet: typeof row.snippet === "string" && row.snippet.trim() ? row.snippet : null,
            }];
        }),
        nextBefore: typeof body.next_cursor === "string" && body.next_cursor ? body.next_cursor : null,
    };
}

/** Markdown read as the words it shows: an agent's answer is markdown, and
 *  `**Constraints**` in a result is noise. Links and images keep their
 *  words; code keeps its text; the marks go. */
export function plainOf(markdown: string): string {
    return markdown
        .replace(/```[^\n]*\n?/g, " ")
        .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
        .replace(/`([^`]*)`/g, "$1")
        .replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)/gm, "")
        .replace(/(\*\*|__|~~)(?=\S)([\s\S]*?\S)\1/g, "$2")
        .replace(/(^|[^\w*])[*_](?=\S)([^*_\n]*?\S)[*_](?!\w)/g, "$1$2")
        .replace(/^\s*(?:[-*_]\s*){3,}$/gm, " ");
}

/** The words of a line around what was searched for, the first match in the
 *  middle, cut to about `width` at word edges, its markdown read as plain
 *  words -- as pieces, so a view can mark the matches without parsing
 *  HTML. */
export function snippet(text: string, query: string, width = 160): { text: string; match: boolean }[] {
    const flat = plainOf(text).replace(/\s+/g, " ").trim();
    const words = query.toLowerCase().split(/\s+/).filter((word) => word.length > 0);
    const lower = flat.toLowerCase();
    const first = words.length ? Math.min(...words.map((word) => lower.indexOf(word)).filter((at) => at >= 0), flat.length) : 0;
    let start = first > width / 2 && first < flat.length ? Math.max(0, first - Math.floor(width / 3)) : 0;
    let end = Math.min(flat.length, start + width);
    /* Never half a word: start after a space, end before one. */
    if (start > 0) {
        const space = flat.indexOf(" ", start - 1);
        if (space >= 0 && space < first) start = space + 1;
    }
    if (end < flat.length) {
        const space = flat.lastIndexOf(" ", end);
        if (space > Math.max(start, first)) end = space;
    }
    const cut = (start > 0 ? "…" : "") + flat.slice(start, end) + (end < flat.length ? "…" : "");
    if (!words.length) return [{ text: cut, match: false }];
    const pattern = new RegExp("(" + words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
    return cut.split(pattern).filter((piece) => piece !== "").map((piece) => ({ text: piece, match: words.includes(piece.toLowerCase()) }));
}

/** What a result shows: the line's own words where the reader has them --
 *  whole, so the cut is made here -- and the server's cut where not. */
export function hitText(hit: Pick<SearchHit, "line" | "snippet">): string {
    return hit.line.text ?? hit.snippet ?? "";
}
