import type { PickedFile } from "../../data/io";
import type { CommentRow } from "@web/docpages/comments/model";
import type { StandingJob } from "@web/schedule/schedules";
import type { FileContent, LibraryItem, ResourcePage } from "./web/types";

export type { FileContent, LibraryItem, ResourcePage } from "./web/types";
export type { StandingJob };
export type { CommentRow } from "@web/docpages/comments/model";

/** Files, pages and docs (PARITY.md, C).
 *
 *  Everything these screens read or change, behind one interface with a
 *  live side (`live.ts`) and a sample side (`sample.ts`). Screens reach it
 *  only through `useArea("files")`, never the SDK directly, so sample data
 *  and a live account are the same screens. The page editor, which runs in a
 *  WebView, reaches it the same way: each call it makes is answered from
 *  here (`web/protocol.ts`). */

/** A search result: the file, and the passage that matched. */
export interface SearchHit {
    path: string;
    snippet: string;
    page: number | null;
}

/** A public link to a file: anyone holding it can open the file, until it
 *  expires or has been opened `maxHits` times. */
export interface SharedLink {
    code: string;
    /** The address to send: the web app's reader for this code. */
    readUrl: string;
    expiresAt: string;
    maxHits: number;
}

/** What a file's editor polls to see a change made elsewhere. */
export interface FileStamp {
    sha: string | null;
    updatedAt: string | null;
}

/** Someone a comment can name: an agent of the Lemma, or a person in it. */
export interface Mentionable {
    kind: "agent" | "person";
    label: string;
    /** The agent's wire name (`POD_DEFAULT` for the Lemma itself), or the
     *  person's user id. */
    key: string;
}

/** One firing of a comment wake-up: what the named agent did about it. */
export interface WakeRun {
    status: string;
    tone: "ok" | "bad" | "off" | "warn";
    error: string;
    subjectId: string;
    targetRunId: string;
    targetKind: string;
}

export type CommentsStatus = "ready" | "missing" | "forbidden";

export interface FilesSource {
    /* ── reading ─────────────────────────────────────────────────────── */
    list(podId: string, directory: string, page?: string): Promise<ResourcePage<LibraryItem>>;
    search(podId: string, query: string, within: string): Promise<SearchHit[]>;
    /** A file, ready to show: its text when it is read inline, a signed URL
     *  when it is drawn from one. */
    read(podId: string, path: string): Promise<FileContent>;
    /** A file's bytes as base64, for what is drawn from bytes (a PDF). */
    bytes(podId: string, path: string): Promise<string>;
    stamp(podId: string, path: string): Promise<FileStamp>;
    /** A short-lived link to the bytes. */
    signedUrl(podId: string, path: string): Promise<string | null>;
    /** Why a file could not be read. */
    why(podId: string, path: string): Promise<string>;
    /** Onto the phone, for another app or the share sheet: its local URI. */
    toPhone(podId: string, path: string): Promise<string>;

    /* ── changing ────────────────────────────────────────────────────── */
    /** Into `directory`; a file already there by that name is replaced. */
    upload(podId: string, directory: string, file: PickedFile): Promise<LibraryItem>;
    /** The same, from bytes the page editor hands over. */
    uploadBytes(podId: string, directory: string, file: { base64: string; name: string; type: string }, searchEnabled: boolean): Promise<LibraryItem>;
    makeFolder(podId: string, parent: string, name: string): Promise<LibraryItem>;
    /** Rename or move: the item at `from` is at `to` afterwards. */
    move(podId: string, from: string, to: string): Promise<void>;
    remove(podId: string, path: string): Promise<void>;
    /** Read a failed file again; answers the status it settled on. */
    retry(podId: string, path: string): Promise<string>;
    /** Write text over a file, or make it where there is none. */
    writeText(podId: string, path: string, text: string): Promise<void>;
    /** Make a file only where there is none: a taken path rejects with
     *  `statusCode` 409, so the caller can choose another name. */
    createText(podId: string, path: string, text: string): Promise<void>;

    /* ── what a page's blocks read ───────────────────────────────────── */
    tableNames(podId: string): Promise<string[]>;
    tableColumns(podId: string, table: string): Promise<{ name: string }[]>;
    tableRows(podId: string, table: string): Promise<ResourcePage<Record<string, unknown>>>;
    runQuery(podId: string, sql: string): Promise<{ items: Record<string, unknown>[]; truncated: boolean }>;

    /* ── sharing ─────────────────────────────────────────────────────── */
    /** The permanent link for people already in the Lemma. */
    appUrl(podId: string, path: string): Promise<string | null>;
    links(podId: string, path: string): Promise<SharedLink[]>;
    share(podId: string, path: string, bounds: { expiresSeconds: number; maxHits: number }): Promise<SharedLink>;
    revoke(podId: string, code: string): Promise<boolean>;

    /* ── a page's conversation ───────────────────────────────────────── */
    /** Say this to the Lemma in the page's own conversation (made on first
     *  use), and answer its id. */
    askAboutPage(podId: string, path: string, text: string): Promise<string>;
    /** The page's conversation, if it has one. */
    pageConversation(podId: string, path: string): Promise<string | null>;
    /** A renamed page keeps its conversation: bound by path, it follows. */
    movePageConversation(podId: string, from: string, to: string): Promise<void>;

    /* ── comments ────────────────────────────────────────────────────── */
    commentsStatus(podId: string): Promise<CommentsStatus>;
    enableComments(podId: string): Promise<void>;
    comments(podId: string, path: string): Promise<CommentRow[]>;
    addComment(podId: string, data: Record<string, unknown>, author: { id: string | null; name: string }): Promise<CommentRow>;
    updateComment(podId: string, id: string, patch: Record<string, unknown>): Promise<void>;
    deleteComment(podId: string, id: string): Promise<void>;
    moveComments(podId: string, from: string, to: string): Promise<void>;
    /** Who a comment can name. */
    mentionable(podId: string, lemmaName: string): Promise<Mentionable[]>;
    /** Tell a person they were named, wherever they last talked to the Lemma. */
    notify(podId: string, userId: string, text: string): Promise<void>;
    wakes(podId: string): Promise<StandingJob[]>;
    wakeRuns(podId: string, jobId: string): Promise<WakeRun[]>;
    /** Let an agent answer comments: make its wake-up, or turn it back on. */
    turnOnWake(podId: string, agent: { key: string; label: string }, job: StandingJob | null): Promise<void>;
    askAgain(podId: string, id: string, agentKey: string): Promise<void>;
}
