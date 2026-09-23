/** What a widget or an app is allowed to say to the app.
 *
 *  A framed view cannot send a message itself. It holds a real SDK and could
 *  create a conversation and stream one, but the reply would arrive *inside
 *  the frame* — a 480px box on the stage — while the thread beside it showed
 *  nothing. So the frame asks, and the app does it, through the same path
 *  the composer uses.
 *
 *  The app *fills the composer*; it does not send. Whatever lands in a thread
 *  carries a person's name, and a widget renders content nobody in the pod
 *  wrote — a row out of a table, a page the agent fetched. Filling the box
 *  costs the person one keystroke and buys them the edit, and it means no
 *  markup can put words in their mouth. `newConversation` picks which composer
 *  gets filled; nothing here ever sends.
 *
 *  The wire names are the SDK's (`browser-compose.ts` in lemma-typescript),
 *  repeated rather than imported: the published `lemma-sdk` this app depends on
 *  does not carry them yet, and a string is a smaller thing to keep in step
 *  than a version bump.
 */

export const COMPOSE_MESSAGE_TYPE = "lemma-compose";
export const COMPOSE_RESULT_MESSAGE_TYPE = "lemma-compose-result";

export interface ComposeRequest {
    /** Correlates the app's acknowledgement with the frame's call. */
    id: string;
    text: string;
    /** Fill a new conversation's composer rather than the open one. */
    newConversation: boolean;
}

/** Nobody wants a composer holding an essay, and nobody typed one. */
const MAX_TEXT = 4000;

/* Which windows the app is actually showing. A widget sits several
   components deep in a transcript and an app is a tab on the stage, so the one
   listener that serves both cannot reach either frame by ref. Each frame
   registers itself instead, and a message from a window that is not in here is
   from a frame this app did not put on screen. */
const frames = new Set<Window>();

export function registerFrame(view: Window | null | undefined): () => void {
    if (!view) return () => undefined;
    frames.add(view);
    return () => { frames.delete(view); };
}

/* Membership is the whole check — an `instanceof Window` in front of it adds
   nothing a registered window would not already satisfy, and costs the module
   its testability outside a browser. */
export function isPodFrame(view: unknown): boolean {
    return typeof view === "object" && view !== null && frames.has(view as Window);
}

/** The request in `event`, or null when it is not one, or not from our frame. */
export function readComposeRequest(event: MessageEvent): ComposeRequest | null {
    if (!isPodFrame(event.source)) return null;
    const data = event.data as Partial<ComposeRequest> & { type?: unknown } | null;
    if (!data || typeof data !== "object" || data.type !== COMPOSE_MESSAGE_TYPE) return null;
    if (typeof data.text !== "string") return null;
    const text = data.text.trim();
    if (!text) return null;
    return {
        id: typeof data.id === "string" ? data.id : "",
        text: text.slice(0, MAX_TEXT),
        newConversation: data.newConversation === true,
    };
}

/** Tell the frame the app took it, so a view with no composer can say so.
 *
 *  Without this a button on a page nobody framed is indistinguishable from a
 *  button that worked: the message goes nowhere and the click looks lost. The
 *  reply is addressed to the frame's own origin rather than "*", because it is
 *  the app speaking and only the frame that asked should hear it.
 */
export function acknowledge(event: MessageEvent, request: ComposeRequest): void {
    const view = event.source;
    if (!isPodFrame(view) || !event.origin) return;
    try {
        (view as Window).postMessage(
            { type: COMPOSE_RESULT_MESSAGE_TYPE, id: request.id, ok: true },
            event.origin,
        );
    } catch { /* the frame went away between asking and being answered */ }
}
