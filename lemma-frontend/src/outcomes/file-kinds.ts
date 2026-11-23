/** What the Files page says about a file or a folder, worked out from what
 *  the listing carries: which kind of thing it is, in a word and a mark;
 *  how big it is; whether it is one of the Lemma's own folders, which have
 *  pages of their own; and where in the folders it sits.
 *
 *  Nothing here reaches the network or the page, so all of it is tested
 *  directly (`tests/file-kinds.test.ts`). */

import type { LibraryItem } from "@/data/types";
import { describeSize } from "@/thread/attachments";
import { isMemoryPath } from "@/thread/memory-notes";

/** A row of a folder's listing, with the two things this page shows that
 *  the library's own listing leaves out: how big it is, and the type it was
 *  stored with. Both are absent where the source does not say (the sample). */
export type FileItem = LibraryItem & { size?: number; mime?: string };

/** The marks a row can wear: a coloured square with a glyph. */
export type MarkKind = "folder" | "spreadsheet" | "pdf" | "document" | "markdown" | "image" | "audio" | "video" | "other";

export interface Kind {
    mark: MarkKind;
    /** The kind in words, never a mime type: "PDF", "Spreadsheet". */
    word: string;
}

const KINDS: Record<string, Kind> = {};
function kinds(mark: MarkKind, word: string, extensions: string) {
    for (const extension of extensions.split(" ")) KINDS[extension] = { mark, word };
}
kinds("spreadsheet", "Spreadsheet", "csv tsv xlsx xls ods numbers");
kinds("pdf", "PDF", "pdf");
kinds("document", "Document", "doc docx odt rtf pages");
kinds("document", "Text", "txt log");
kinds("document", "Web page", "html htm");
kinds("document", "Slides", "pptx ppt odp key");
kinds("markdown", "Markdown", "md markdown mdx");
kinds("image", "Image", "png jpg jpeg gif webp svg heic avif bmp tif tiff");
kinds("audio", "Audio", "mp3 wav m4a aac ogg oga flac opus");
kinds("video", "Video", "mp4 mov webm m4v ogv avi mkv");
kinds("other", "Data", "json yaml yml xml");
kinds("other", "Archive", "zip gz tgz tar rar 7z");
kinds("other", "Code", "py ts tsx js jsx mjs sh css sql go rs rb java");

const EXTENSION = /\.([a-z0-9]{1,8})$/i;
/** `detail` falls back to the mime type when a file has no description;
 *  this is how one is told from a description. */
const MIME = /^[a-z]+\/[\w.+-]+$/i;

/** Which kind of thing this is: by its name's extension first, since that
 *  is what the person chose, then by the type it was stored with. */
export function kindOf(item: Pick<FileItem, "name" | "kind"> & { mime?: string; detail?: string }): Kind {
    if (item.kind === "folder") return { mark: "folder", word: "Folder" };
    if (item.kind === "table") return { mark: "spreadsheet", word: "Table" };
    const extension = EXTENSION.exec(item.name)?.[1]?.toLowerCase();
    if (extension && KINDS[extension]) return KINDS[extension];
    const mime = (item.mime || (MIME.test(item.detail ?? "") ? item.detail ?? "" : "")).toLowerCase();
    if (mime.startsWith("image/")) return KINDS.png;
    if (mime.startsWith("video/")) return KINDS.mp4;
    if (mime.startsWith("audio/")) return KINDS.mp3;
    if (mime.includes("pdf")) return KINDS.pdf;
    if (mime.includes("markdown")) return KINDS.md;
    if (/spreadsheet|excel|csv/.test(mime)) return KINDS.xlsx;
    if (/wordprocessing|msword|opendocument\.text/.test(mime)) return KINDS.docx;
    if (/presentation|powerpoint/.test(mime)) return KINDS.pptx;
    if (mime.includes("html")) return KINDS.html;
    if (/json|yaml|xml/.test(mime)) return KINDS.json;
    if (/zip|compressed|tar/.test(mime)) return KINDS.zip;
    if (mime.startsWith("text/")) return KINDS.txt;
    return { mark: "other", word: "File" };
}

/** What a folder holds, where its own listing has been read whole. */
export interface FolderCount {
    files: number;
    folders: number;
}

function counted(count: number, one: string): string {
    return count + " " + (count === 1 ? one : one + "s");
}

/** "3 files", "1 folder, 2 files", "Empty". */
export function countWords(count: FolderCount): string {
    if (count.files === 0 && count.folders === 0) return "Empty";
    if (count.folders === 0) return counted(count.files, "file");
    if (count.files === 0) return counted(count.folders, "folder");
    return counted(count.folders, "folder") + ", " + counted(count.files, "file");
}

/** The kind in words with what is known beside it: "PDF · 1.2 MB",
 *  "Folder · 3 files", or the word alone where nothing more is known. */
export function kindLine(item: FileItem, count?: FolderCount | null): string {
    const { word } = kindOf(item);
    if (item.kind === "folder") return count ? word + " · " + countWords(count) : word;
    return typeof item.size === "number" ? word + " · " + describeSize(item.size) : word;
}

/** A folder's path without the slash a listing may end it with. */
export function folderPath(path: string): string {
    return path.replace(/\/+$/, "") || "/";
}

/** The folder something is in. */
export function parentOf(path: string): string {
    const clean = folderPath(path);
    return clean.slice(0, clean.lastIndexOf("/")) || "/";
}

/** The Lemma's own folders at the top of its files, each with a page of
 *  its own (Memory, Skills, Pages), or nobody's to open (`system`). `me`
 *  is the reader's own files, which are the other chip. */
const OWN_AT_TOP = new Set(["memory", "skills", "pages", "me", "system"]);
const NEVER = new Set(["node_modules", "__pycache__"]);

/** Whether the Files list leaves this out: one of the Lemma's own folders,
 *  its notes about the reader in their own files, or a dot-name. */
export function isLemmasOwn(path: string): boolean {
    const parts = path.split("/").filter(Boolean);
    if (parts.some((part) => part.startsWith(".") || NEVER.has(part))) return true;
    if (parts.length === 1 && OWN_AT_TOP.has(parts[0])) return true;
    if (parts[0] === "me" && parts.length === 2 && (parts[1] === "agents" || parts[1] === "AGENTS.md")) return true;
    return isMemoryPath("/" + parts.join("/"));
}

/** Whose files: everyone's in the Lemma, or the reader's own, which the
 *  datastore keeps under `/me` for whoever is asking. */
export type Whose = "everyone" | "mine";

export const ROOTS: Record<Whose, { path: string; label: string }> = {
    everyone: { path: "/", label: "Files" },
    mine: { path: "/me", label: "My files" },
};

export interface Step {
    label: string;
    path: string;
}

/** Where a folder is, as the steps from the top: Files › Proposals › 2026. */
export function crumbsOf(directory: string, whose: Whose): Step[] {
    const root = ROOTS[whose];
    const at = folderPath(directory);
    const steps: Step[] = [{ label: root.label, path: root.path }];
    if (at === root.path) return steps;
    const rest = root.path === "/" ? at.slice(1) : at.slice(root.path.length + 1);
    let path = root.path;
    for (const part of rest.split("/").filter(Boolean)) {
        path = path === "/" ? "/" + part : path + "/" + part;
        steps.push({ label: part, path });
    }
    return steps;
}

/** Whether a folder is the one shown or somewhere under it, among the
 *  reader's chosen files: everyone's top folder does not hold `/me`. */
export function isWithin(folder: string, directory: string, whose: Whose): boolean {
    const at = folderPath(folder);
    const here = folderPath(directory);
    if (whose === "everyone" && (at === "/me" || at.startsWith("/me/"))) return false;
    return at === here || at.startsWith(here === "/" ? "/" : here + "/");
}

/** What the datastore says about reading a file, as the sheet says it.
 *  `skipped` is a kind it does not read ahead (`NOT_REQUIRED`); null is a
 *  status this build does not know, or none, and says nothing. */
export type ReadState = "read" | "reading" | "failed" | "skipped";

export function readStateOf(status: string | null | undefined): ReadState | null {
    switch ((status ?? "").toUpperCase()) {
        case "COMPLETED":
            return "read";
        case "PENDING":
        case "PROCESSING":
            return "reading";
        case "FAILED":
        case "FAILED_PERMANENT":
            return "failed";
        case "NOT_REQUIRED":
            return "skipped";
        default:
            return null;
    }
}

/** The start of a text file, for a look before opening it: any front
 *  matter left off, cut at a word near `limit`. */
export function excerptOf(text: string, limit = 700): string {
    const body = text.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "").trim();
    if (body.length <= limit) return body;
    const cut = body.slice(0, limit);
    const space = cut.lastIndexOf(" ");
    return (space > limit * 0.6 ? cut.slice(0, space) : cut).trimEnd() + "…";
}

/** Folders first, then the most recently changed. */
export function inListOrder<T extends Pick<LibraryItem, "kind" | "updated">>(items: readonly T[]): T[] {
    return [...items].sort((a, b) => Number(b.kind === "folder") - Number(a.kind === "folder") || b.updated.localeCompare(a.updated));
}
