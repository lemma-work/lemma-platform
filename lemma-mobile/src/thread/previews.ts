/** What a thread shows of a file or a table without opening it: the web's
 *  rules from `data/live.ts` (`fileKind`, the inline limits) and
 *  `thread/data-view.tsx` (the first rows and columns, and what is left out,
 *  said plainly). Pure, so the tests read it directly. */

export type FileKind = "image" | "video" | "audio" | "pdf" | "html" | "markdown" | "text" | "binary";

/** The extension as well as the type: a file uploaded without one arrives as
 *  `application/octet-stream`, and an mp4 is still a video. */
export function fileKind(mime: string, path: string): FileKind {
    const ext = (path.split(".").pop() ?? "").toLowerCase();
    if (mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "heic"].includes(ext)) return "image";
    if (mime.startsWith("video/") || ["mp4", "webm", "mov", "m4v", "ogv"].includes(ext)) return "video";
    if (mime.startsWith("audio/") || ["mp3", "wav", "m4a", "aac", "ogg", "oga", "flac", "opus"].includes(ext)) return "audio";
    if (mime === "application/pdf" || ext === "pdf") return "pdf";
    if (mime.includes("html") || ext === "html" || ext === "htm") return "html";
    if (mime.includes("markdown") || ext === "md" || ext === "markdown") return "markdown";
    if (mime.startsWith("text/") || ["txt", "csv", "json", "yaml", "yml", "log"].includes(ext)) return "text";
    return "binary";
}

/** Past this a text file is not read into a chat at all. */
export const INLINE_TEXT_LIMIT = 512 * 1024;

/** How much of a text file a chat shows before "Open" is the way to the
 *  rest: about a screen of a phone. */
export const EXCERPT_CHARS = 900;

/** The start of a text, cut at a line or a word near `limit`, and whether
 *  anything was cut. */
export function excerptOf(text: string, limit = EXCERPT_CHARS): { text: string; cut: boolean } {
    const whole = text.replace(/\s+$/, "");
    if (whole.length <= limit) return { text: whole, cut: false };
    const head = whole.slice(0, limit);
    const line = head.lastIndexOf("\n");
    const word = head.lastIndexOf(" ");
    const at = line > limit * 0.6 ? line : word > limit * 0.6 ? word : limit;
    return { text: head.slice(0, at).replace(/\s+$/, "") + "…", cut: true };
}

/** The `---` block at the top of a markdown file, held apart: a renderer
 *  turns it into a rule and a heading, and that would open every skill file
 *  with its own frontmatter as the headline. */
export function withoutFrontmatter(text: string): string {
    const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(text);
    return match ? text.slice(match[0].length) : text;
}

export function readableSize(bytes: number): string {
    if (!bytes) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

/** "ZIP", not "binary": the reader knows a file by its extension. */
export function formatOf(name: string): string {
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot + 1).toUpperCase() : "File";
}

/* ── a table or a query, as a few rows ─────────────────────────────────── */

/** The web's preview: past this a reader is scrolling a card instead of
 *  reading an answer, and the table itself is where scrolling belongs. */
export const PREVIEW_ROWS = 8;
export const PREVIEW_COLUMNS = 6;

export function cellText(value: unknown): string {
    if (value === null || value === undefined) return "";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
}

export interface DataPreview {
    columns: string[];
    rows: string[][];
    /** "8 of 50 rows · 3 more columns", or empty when everything is shown. */
    footnote: string;
    /** The server cut the result at its row cap: there are more rows than
     *  any count here says. */
    capped: boolean;
}

export function dataPreview(items: readonly Record<string, unknown>[], truncated = false): DataPreview {
    const fields = Array.from(new Set(items.flatMap((row) => Object.keys(row))));
    const columns = fields.slice(0, PREVIEW_COLUMNS);
    const hidden = fields.length - columns.length;
    const visible = items.slice(0, PREVIEW_ROWS);
    const footnotes = [
        items.length > visible.length ? `${visible.length} of ${items.length} rows` : "",
        hidden > 0 ? `${hidden} more column${hidden === 1 ? "" : "s"}` : "",
    ].filter(Boolean);
    return {
        columns,
        rows: visible.map((row) => columns.map((field) => cellText(row[field]))),
        footnote: footnotes.join(" · "),
        capped: truncated,
    };
}
