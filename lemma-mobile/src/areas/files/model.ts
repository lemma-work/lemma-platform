import type { FileContent, LibraryItem, SearchHit, SharedLink, WakeRun } from "./source";
import { docTitle } from "@web/library/doc-title";
import { freeName } from "@web/docpages/templates";

/** The files screens' decisions, apart from drawing: what kind a file is,
 *  how a listing is ordered, what its rows say. No React here, so the tests
 *  can hold all of it. Where the web decides the same thing, this says it the
 *  web's way (`lemma-frontend/src/data/live.ts`, `library/*`). */

/** Text inlined for reading: past this a file is offered, not drawn. */
export const INLINE_TEXT_LIMIT = 512 * 1024;
export const INLINE_HTML_LIMIT = 4 * 1024 * 1024;
/** What the web refuses to upload (`thread/attachments.ts`). */
export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;
/** A PDF past this is offered to save, not drawn. */
export const PDF_LIMIT = 50 * 1024 * 1024;

/** How a file is drawn, from its MIME type and its name (the web's
 *  `fileKind`): a file uploaded without a type arrives as octet-stream, and an
 *  mp4 that says "binary" should still play. */
export function kindOf(mime: string, path: string): FileContent["kind"] {
    const ext = (path.split(".").pop() ?? "").toLowerCase();
    if (mime.startsWith("image/")) return "image";
    if (mime.startsWith("video/") || ["mp4", "webm", "mov", "m4v", "ogv"].includes(ext)) return "video";
    if (mime.startsWith("audio/") || ["mp3", "wav", "m4a", "aac", "ogg", "oga", "flac", "opus"].includes(ext)) return "audio";
    if (mime === "application/pdf" || ext === "pdf") return "pdf";
    if (mime.includes("html") || ext === "html" || ext === "htm") return "html";
    if (mime.includes("markdown") || ext === "md" || ext === "markdown") return "markdown";
    if (mime.startsWith("text/") || ["txt", "csv", "json", "yaml", "yml", "log"].includes(ext)) return "text";
    if (["png", "jpg", "jpeg", "gif", "webp", "heic", "svg"].includes(ext)) return "image";
    return "binary";
}

/** Read inline (the bytes are the view), as opposed to drawn from a link. */
export function readsInline(kind: FileContent["kind"]): boolean {
    return kind === "markdown" || kind === "text" || kind === "html";
}

export function describeSize(bytes: number): string {
    if (!bytes) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0) + " MB";
}

/** "ZIP", not "binary": a file nothing here draws is known by its extension. */
export function formatOf(name: string): string {
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot + 1).toUpperCase() : "File";
}

export function nameOf(path: string): string {
    return path.split("/").filter(Boolean).pop() ?? path;
}

export function parentOf(path: string): string {
    const trimmed = path.replace(/\/+$/, "");
    const cut = trimmed.lastIndexOf("/");
    return cut > 0 ? trimmed.slice(0, cut) : "/";
}

export function isPage(path: string): boolean {
    return /\.(md|markdown)$/i.test(path);
}

/** Your own files: `/me` and under it. Only you can open them. */
export function isPersonal(path: string): boolean {
    return /^\/me(\/|$)/.test(path);
}

/** What a row is called: a page by its title, a file by a title made from
 *  its name, a folder by its name as given. */
export function titleOf(item: Pick<LibraryItem, "kind" | "name">): string {
    return item.kind === "folder" ? item.name : docTitle(item.name);
}

/** The platform's own bookkeeping, never a person's files. */
export function isInternal(item: Pick<LibraryItem, "path">): boolean {
    return item.path.split("/").some((part) => part.startsWith(".") || ["node_modules", "__pycache__", "system"].includes(part));
}

/** Folders first, then what changed last, as the web lists them. */
export function ordered(items: readonly LibraryItem[]): LibraryItem[] {
    return [...items].sort((a, b) => Number(b.kind === "folder") - Number(a.kind === "folder") || b.updated.localeCompare(a.updated));
}

/** A listing of a folder that is not there: a 404, or the 400 the server
 *  answers for a directory nobody has made yet. */
export function isMissingFolder(error: unknown): boolean {
    const failure = error as { statusCode?: number; message?: string } | null;
    return failure?.statusCode === 404 || (failure?.statusCode === 400 && /not found/i.test(failure.message ?? ""));
}

/** What a file row's glyph shows. */
export type Glyph = "folder" | "page" | "image" | "video" | "audio" | "pdf" | "web" | "data" | "file";

export function glyphOf(item: Pick<LibraryItem, "kind" | "name" | "mime">): Glyph {
    if (item.kind === "folder") return "folder";
    const kind = kindOf(item.mime ?? "", item.name);
    if (kind === "markdown") return "page";
    if (kind === "image" || kind === "video" || kind === "audio" || kind === "pdf") return kind;
    if (kind === "html") return "web";
    if (kind === "text") return "data";
    return "file";
}

/** A listing row, from the server's file summary. */
export function itemOf(raw: Record<string, unknown>): LibraryItem {
    const text = (key: string) => (typeof raw[key] === "string" ? (raw[key] as string) : "");
    const kind = /folder|directory/i.test(text("kind")) ? "folder" : "file";
    const mime = text("mime_type");
    return {
        id: text("id") || text("path"),
        name: text("name") || nameOf(text("path")),
        kind,
        path: text("path"),
        updated: text("updated_at") || text("created_at"),
        detail: text("description") || mime || text("kind"),
        ...(text("description") ? { description: text("description") } : {}),
        status: text("status") || undefined,
        visibility: text("visibility") || undefined,
        owner: text("owner_user_id") || null,
        size: typeof raw.size_bytes === "number" ? raw.size_bytes : undefined,
        mime: mime || undefined,
    };
}

/** The line under a file's name: what it is, in a word, and its size. */
export function detailOf(item: LibraryItem): string {
    if (item.kind === "folder") return "Folder";
    const kind = kindOf(item.mime ?? "", item.name);
    const word = kind === "markdown" ? "Page" : kind === "image" ? "Image" : kind === "video" ? "Video" : kind === "audio" ? "Audio"
        : kind === "pdf" ? "PDF" : kind === "html" ? "Web page" : formatOf(item.name);
    return [item.description || word, item.size ? describeSize(item.size) : ""].filter(Boolean).join(" · ");
}

/** Search answers passages, several per file: one row per file, its best. */
export function hitsOf(items: readonly { path: string; content: string; score: number; page_number?: number | null }[]): SearchHit[] {
    const best = new Map<string, { hit: SearchHit; score: number }>();
    for (const item of items) {
        const held = best.get(item.path);
        if (held && held.score >= item.score) continue;
        best.set(item.path, { hit: { path: item.path, snippet: snippetOf(item.content), page: item.page_number ?? null }, score: item.score });
    }
    return [...best.values()].sort((a, b) => b.score - a.score).map((one) => one.hit);
}

/** A passage as one readable line: markup and runs of space taken out. */
export function snippetOf(content: string): string {
    return content.replace(/[#*_`>|]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 220);
}

/** The web app's reader for a public link's code. */
export function readUrlOf(web: string, code: string): string {
    return web.replace(/\/$/, "") + "/d/" + code;
}

export function sharedLinkOf(web: string, raw: { signed_url?: string; code?: string; expires_at?: string; max_hits?: number }): SharedLink {
    /* The code is the last segment of the URL the server minted; taken from
       it rather than built here, so a change of shape cannot leave this wrong. */
    const code = raw.code ?? (raw.signed_url ?? "").split("/").filter(Boolean).pop() ?? "";
    return { code, readUrl: readUrlOf(web, code), expiresAt: raw.expires_at ?? "", maxHits: raw.max_hits ?? 0 };
}

/** "Expires in 3 hours", from the bound the server came back with. */
export function expiresIn(iso: string, now: number): string {
    const left = Date.parse(iso) - now;
    if (!Number.isFinite(left)) return "";
    if (left <= 0) return "Expired";
    if (left < 3_600_000) return "Expires in under an hour";
    const hours = Math.round(left / 3_600_000);
    if (hours < 48) return "Expires in " + hours + (hours === 1 ? " hour" : " hours");
    return "Expires in " + Math.round(hours / 24) + " days";
}

/** The folders between a location's root and a folder, each with its path:
 *  `/Notes/2026` under Shared is Shared › Notes › 2026. */
export function crumbs(path: string, root: string): { name: string; path: string }[] {
    const inside = root === "/" ? path : path.slice(root.length);
    const parts = inside.split("/").filter(Boolean);
    return parts.map((name, at) => ({ name, path: (root === "/" ? "" : root) + "/" + parts.slice(0, at + 1).join("/") }));
}

/** Where a move can put something: not inside itself. */
export function canMoveInto(item: Pick<LibraryItem, "kind" | "path">, folder: string): boolean {
    if (parentOf(item.path) === folder) return false;
    if (item.kind !== "folder") return true;
    return folder !== item.path && !folder.startsWith(item.path + "/");
}

const RUN_TONES: Record<string, WakeRun["tone"]> = {
    COMPLETED: "ok", DISPATCHED: "ok", RECEIVED: "warn", PROCESSING: "warn",
    CANCELLED: "off", FILTERED: "off", FAILED: "bad", DEAD_LETTERED: "bad", TARGET_FAILED: "bad",
};

/** One firing of a wake-up (the web's schedule `readRun`, the parts a comment shows). */
export function wakeRunOf(raw: Record<string, unknown>): WakeRun {
    const text = (value: unknown) => (typeof value === "string" ? value : "");
    const record = (value: unknown) => (value && typeof value === "object" ? (value as Record<string, unknown>) : {});
    const status = text(raw.status).toUpperCase();
    return {
        status,
        tone: RUN_TONES[status] ?? "warn",
        error: [text(raw.error_type), text(raw.error_code)].filter(Boolean).join(" · "),
        subjectId: text(record(raw.payload).id) || text(record(raw.metadata).record_id),
        targetRunId: text(raw.target_run_id),
        targetKind: text(raw.target_kind).toLowerCase(),
    };
}

/** What a named agent is doing about a comment, in one line. */
export function wakeLine(name: string, run: WakeRun | null, agedMs: number): string {
    if (run?.tone === "bad") return name + " couldn’t answer" + (run.error ? ": " + run.error : "") + ".";
    if (run?.status === "FILTERED") return name + " passed on this one.";
    if (run?.status === "COMPLETED") return name + " finished. Its reply is on the way.";
    if (run?.status === "DISPATCHED") return name + " is working on it.";
    if (run) return name + " is starting.";
    return agedMs > 90_000 ? name + " hasn’t picked this up." : name + " will pick this up in a moment.";
}

/** Where a new page goes from here: the Lemma's pages folder from the top
 *  of its shared files, as on the web, and the folder itself anywhere else,
 *  so a page made inside "Notes" or in your own files is where you made it. */
export function pageFolderFor(directory: string): string {
    return directory === "/" ? "/pages" : directory;
}

/** The web's `makePage`, in any folder: a free name, never over an existing
 *  page. `taken` is a hint; the real guard is `create` refusing a taken path
 *  with a 409, after which the next name is tried. Answers the path made. */
export async function makePageIn(
    create: (path: string, text: string) => Promise<void>,
    folder: string,
    base: string,
    text: string,
    taken: Set<string>,
): Promise<string> {
    const seen = new Set(taken);
    for (let attempt = 0; attempt < 25; attempt++) {
        const name = freeName(base, seen);
        const path = (folder === "/" ? "" : folder) + "/" + name + ".md";
        try {
            await create(path, text);
            return path;
        } catch (error) {
            if ((error as { statusCode?: number } | null)?.statusCode !== 409) throw error;
            seen.add((name + ".md").toLowerCase());
        }
    }
    throw new Error("Couldn’t find a free name for the page. Try again.");
}
