/** What the phone app and the page editor say to each other.
 *
 *  The editor is the web's own (`lemma-frontend/src/thread/document-editor.tsx`
 *  and its extensions), built into one HTML page by `lemma-mobile/webview` and
 *  run in a WebView. It never reaches the network itself: everything it would
 *  have asked the API for is a `call` here, answered by the app with its own
 *  session. No token of the app's is ever handed to the page.
 *
 *  Imported by both sides, so the two cannot drift: the app, and the page's
 *  bridge (`webview/src/editor/bridge.ts`). Plain types and nothing else. */

/** A passage a comment is about: its words and a little either side. */
export interface Anchor {
    quote: string;
    quotePrefix: string;
    quoteSuffix: string;
}

export interface HighlightAnchor extends Anchor {
    id: string;
}

/** What the page asks the app to do for it. Nothing outside this list can be
 *  asked: the page's `lemma()` answers anything else with an error. */
export type CallMethod =
    /* The web's data source. */
    | "readFile" | "writeFile" | "runQuery" | "tableRows" | "listLibrary" | "tableColumns"
    /* The SDK calls the editor makes: `files.get`, `files.getUrl`, `files.upload`. */
    | "fileDetail" | "fileUrl" | "upload"
    /* The page tools: rename the file to follow its title. */
    | "renamePage";

/** A Blob the page hands over, as base64. */
export interface CarriedBlob {
    __blob: string;
    type: string;
    name: string | null;
}

/** The colours the page wears: the app's palette, as it is now. */
export interface PageTheme {
    scheme: "light" | "dark";
    palette: Record<string, string>;
}

export interface EditorInit {
    podId: string;
    path: string;
    text: string;
    /** The Lemma's name, for "Ask … to write". */
    botName: string;
    sample: boolean;
    /** A personal file (`/me`) keeps its name and has no comments. */
    personal: boolean;
    theme: PageTheme;
}

export type FromPage =
    | { type: "ready" }
    | { type: "call"; id: number; method: CallMethod; args: unknown[] }
    /** The save line: "Saving…", "Saved", "Couldn’t save"; null when there
     *  is nothing to say. */
    | { type: "status"; label: string | null; bad: boolean }
    | { type: "focus"; focused: boolean }
    | { type: "open-file"; path: string }
    | { type: "open-table"; name: string }
    /** Words for the Lemma, sent in this page's conversation. */
    | { type: "ask"; text: string }
    | { type: "comment-start"; anchor: Anchor | null }
    | { type: "comment-focus"; id: string | null }
    /** Everything typed has been handed over: the page can go. */
    | { type: "flushed" }
    | { type: "problem"; message: string };

export type ToPage =
    | ({ type: "init" } & EditorInit)
    | { type: "reply"; id: number; ok: true; value: unknown }
    | { type: "reply"; id: number; ok: false; message: string; statusCode: number | null }
    /** The file as it is on the server now, read after somebody else wrote it. */
    | { type: "text"; text: string }
    | ({ type: "theme" } & PageTheme)
    | { type: "visible"; visible: boolean }
    | { type: "comments"; anchors: HighlightAnchor[]; active: string | null; enabled: boolean }
    | { type: "retry" }
    | { type: "blur" }
    /** Save what is typed now, then say `flushed`. */
    | { type: "flush" };
