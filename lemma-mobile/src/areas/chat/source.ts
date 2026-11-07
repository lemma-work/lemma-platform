import type { PickedFile } from "../../data/io";
import type { FileKind } from "../../thread/previews";
import type { Choice, Runtime } from "../../thread/runtimes";

/** The chat thread: a conversation screen, its cards, its composer (PARITY.md, A).
 *
 *  Everything these screens read or change besides the conversation's own
 *  stream (which is the SDK's session, `thread/use-chat.ts`), behind one
 *  interface with a live side (`live.ts`) and a sample side (`sample.ts`).
 *  Screens reach it only through `useArea("chat")`, never the SDK directly,
 *  so sample data and a live account are the same screens. */

/** Rows a table or a query answered. `truncated` is the server's row cap. */
export interface Rows {
    items: Record<string, unknown>[];
    truncated: boolean;
}

/** A file, as a card in a chat draws it. */
export interface ShownFile {
    name: string;
    path: string;
    kind: FileKind;
    size: number;
    mime: string;
    /** A signed address for the bytes, for an image, video or PDF: it needs
     *  no headers. Null when there is none (sample data, or it could not be
     *  minted). */
    url: string | null;
    /** The words, for markdown and text small enough to read here. */
    text: string | null;
    /** What happened, when the file could not be read whole. */
    note: string | null;
    /** How many pages a document has, once the server has read it. */
    pages: number | null;
}

export interface ChatSource {
    tableRows(podId: string, name: string): Promise<Rows>;
    runQuery(podId: string, sql: string): Promise<Rows>;
    file(podId: string, path: string): Promise<ShownFile>;
    /** A document's first page as a picture on this phone (the server
     *  renders it), or null when it has none. */
    firstPage(podId: string, path: string): Promise<string | null>;
    /** What the server read a document as, in markdown: a spreadsheet's
     *  rows as a table. Null when it has not. */
    converted(podId: string, path: string): Promise<string | null>;

    /** The organization's runtimes, retired ones included (`archived`). */
    runtimes(orgId: string): Promise<Runtime[]>;
    /** What a Lemma with no pick of its own runs on. */
    inheritedRuntime(orgId: string): Promise<Choice | null>;
    /** The Lemma's own pick. */
    podRuntime(podId: string): Promise<Choice | null>;
    /** Whether the agent a chat is with names its own model, which the
     *  picker must then not offer to change. */
    agentPinsRuntime(podId: string, agentId: string): Promise<boolean>;
    conversationRuntime(podId: string, conversationId: string): Promise<Choice | null>;
    setConversationRuntime(podId: string, conversationId: string, choice: Choice | null): Promise<void>;

    /** A null title lets the conversation name itself again. */
    rename(podId: string, conversationId: string, title: string | null): Promise<void>;
    archive(podId: string, conversationId: string): Promise<void>;

    /** The conversation's working directory, `pod_cwd`, where its attached
     *  files go. Read off the conversation, never rebuilt. */
    workingFolder(podId: string, conversationId: string): Promise<string | null>;
    /** Upload a file from the phone into that folder; answers where it landed. */
    upload(podId: string, file: PickedFile, directory: string): Promise<{ name: string; path: string }>;
}
