/** The shapes the copied web files take from the web's data layer
 *  (`lemma-frontend/src/data/types.ts`, `schedule/schedules.ts`), kept here so
 *  the copies need nothing else from it. The same names, the same fields. */

/** A file or a folder as a listing draws it. */
export interface LibraryItem {
    id: string;
    name: string;
    kind: "file" | "folder" | "table";
    path: string;
    updated: string;
    detail: string;
    /** The datastore's reading status: PENDING, PROCESSING, FAILED, … */
    status?: string;
    description?: string;
    /** PERSONAL, POD, RESTRICTED or PUBLIC. */
    visibility?: string;
    owner?: string | null;
    /** Bytes; the phone's addition, for the line under a file. */
    size?: number;
    mime?: string;
}

export interface ResourcePage<T> {
    items: T[];
    next?: string | null;
}

/** A file, read to be shown. */
export interface FileContent {
    name: string;
    path: string;
    mime: string;
    size: number;
    /** How it is drawn. */
    kind: "markdown" | "html" | "image" | "video" | "audio" | "pdf" | "text" | "binary";
    /** Set for markdown, text and html read inline. */
    text?: string;
    /** Why it is not drawn, when it is not. */
    note?: string;
    /** The permanent link into the workspace, for people already in the Lemma. */
    appUrl?: string;
    /** A short-lived link to the bytes: what images, video and audio play from. */
    rawUrl?: string;
}

