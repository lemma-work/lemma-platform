import type { Part } from "./model";

/** A person's line, cut into what it draws: plain words, a chip for each
 *  person or agent it names, and a link for each address in it.
 *
 *  A mention part carries an id and the name as it appeared; the text keeps
 *  "@Name" where it was typed. Each "@Name" whose name matches a mention part
 *  becomes that part's chip -- the text is what was said, the part is who was
 *  meant. A link part's label stands in for its bare address; a link with no
 *  part is still a link. Pure, so the tests can hold it to real lines. */

export type Piece =
    | { kind: "text"; text: string }
    | { kind: "mention"; text: string; who: Extract<Part, { type: "mention" }> }
    | { kind: "link"; text: string; url: string };

const URL_PATTERN = /https?:\/\/[^\s<>"')\]]+[^\s<>"')\].,;:!?]/g;

function escaped(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function linked(text: string, labels: ReadonlyMap<string, string>): Piece[] {
    const out: Piece[] = [];
    let at = 0;
    for (const match of text.matchAll(URL_PATTERN)) {
        const start = match.index ?? 0;
        if (start > at) out.push({ kind: "text", text: text.slice(at, start) });
        out.push({ kind: "link", text: labels.get(match[0]) ?? match[0], url: match[0] });
        at = start + match[0].length;
    }
    if (at < text.length) out.push({ kind: "text", text: text.slice(at) });
    return out;
}

export function pieces(text: string, parts: readonly Part[]): Piece[] {
    const mentions = parts.filter((part): part is Extract<Part, { type: "mention" }> => part.type === "mention" && Boolean(part.name));
    const labels = new Map(parts.flatMap((part) => (part.type === "link" && part.label ? [[part.url, part.label] as const] : [])));
    if (!mentions.length) return linked(text, labels);
    /* Longest names first, so "@Priya Shah" is not read as "@Priya". */
    const names = [...new Set(mentions.map((part) => part.name as string))].sort((a, b) => b.length - a.length);
    const pattern = new RegExp("(^|\\s)@(" + names.map(escaped).join("|") + ")(?![\\p{L}\\p{N}_])", "gu");
    const out: Piece[] = [];
    let at = 0;
    for (const match of text.matchAll(pattern)) {
        const start = (match.index ?? 0) + match[1].length;
        if (start > at) out.push(...linked(text.slice(at, start), labels));
        const who = mentions.find((part) => part.name === match[2]);
        if (who) out.push({ kind: "mention", text: "@" + match[2], who });
        at = start + 1 + match[2].length;
    }
    if (at < text.length) out.push(...linked(text.slice(at), labels));
    return out;
}

/** The link parts not already shown in the text, to list under it. */
export function extraLinks(text: string, parts: readonly Part[]): Extract<Part, { type: "link" }>[] {
    return parts.filter((part): part is Extract<Part, { type: "link" }> => part.type === "link" && !text.includes(part.url));
}

/** "12 KB", "3.4 MB": a file's size, as a chip says it. */
export function sizeShown(bytes: number | null): string | null {
    if (bytes === null) return null;
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

/** The kinds of image the server serves inline; anything else is saved. */
const INLINE_IMAGES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

export function isInlineImage(mimeType: string | null): boolean {
    return mimeType !== null && INLINE_IMAGES.has(mimeType.toLowerCase());
}
