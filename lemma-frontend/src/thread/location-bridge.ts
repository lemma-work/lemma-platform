/** Where a framed app says it is, and where it asks to go.
 *
 *  An app is one screen of work and the page for one of its records. The
 *  record it has open is part of where the person is, so it belongs in the
 *  address: a link someone copies should land the next person on the same
 *  record. The address bar and the browser's history are this app's, though,
 *  not the frame's. So the frame says where it moved and this app writes it
 *  down; and when the address changes here -- Back, a link followed -- this
 *  app tells the frame, rather than loading it again.
 *
 *  A frame may also ask for another app of the same Lemma, at a path of its
 *  own: a deal naming its person, who lives in a different app. It asks; this
 *  app, which knows what apps the Lemma has, goes, and answers that it is
 *  going. A frame that hears no answer goes to that app's address itself,
 *  which is right for a page framed somewhere else and wrong here.
 *
 *  Unlike a request for the composer, both are heard from one window only --
 *  the frame of the app in front, which the caller hands in. A widget in a
 *  transcript has no address to move.
 *
 *  The wire names are the SDK's (`browser-location.ts` in lemma-typescript),
 *  repeated rather than imported for the reason `compose-bridge.ts` gives.
 */

export const LOCATION_MESSAGE_TYPE = "lemma-location";
export const OPEN_MESSAGE_TYPE = "lemma-open";
export const OPEN_RESULT_MESSAGE_TYPE = "lemma-open-result";

/** Longer than this is not somewhere an app is. */
const MAX_PATH = 512;

/** An app's name as the Lemma lists it. */
const APP_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

export interface OpenRequest {
    /** Correlates this app's answer with the frame's request. */
    id: string;
    app: string;
    /** Where in that app, as a rooted path. `/` is its one screen. */
    path: string;
}

/** A path inside an app, in the one spelling both sides compare: rooted, no
 *  trailing slash, no empty or dot parts, nothing that would read as a query
 *  or a fragment once it is in an address. Null for anything else. */
export function appPath(value: unknown): string | null {
    if (typeof value !== "string" || !value.startsWith("/") || value.length > MAX_PATH) return null;
    const trimmed = value.length > 1 && value.endsWith("/") ? value.slice(0, -1) : value;
    if (trimmed === "/") return "/";
    const inside = trimmed.slice(1).split("/");
    return inside.every((part) => part !== "" && part !== "." && part !== ".." && !/[?#\\\s]/.test(part)) ? "/" + inside.join("/") : null;
}

function said(event: MessageEvent, from: Window | null, type: string): Record<string, unknown> | null {
    if (!from || event.source !== from) return null;
    const data: unknown = event.data;
    if (!data || typeof data !== "object" || (data as { type?: unknown }).type !== type) return null;
    return data as Record<string, unknown>;
}

/** The path the frame `from` says it moved to, or null when `event` is not
 *  that, or not from it. */
export function readLocation(event: MessageEvent, from: Window | null): string | null {
    const data = said(event, from, LOCATION_MESSAGE_TYPE);
    return data ? appPath(data.path) : null;
}

/** The app the frame `from` asks for, and where in it; null when `event` is
 *  not that, or not from it. */
export function readOpen(event: MessageEvent, from: Window | null): OpenRequest | null {
    const data = said(event, from, OPEN_MESSAGE_TYPE);
    if (!data || typeof data.app !== "string" || !APP_NAME.test(data.app)) return null;
    const path = appPath(data.path ?? "/");
    return path ? { id: typeof data.id === "string" ? data.id : "", app: data.app, path } : null;
}

/** Tell the frame that asked that its app is being opened here, so it does
 *  not go there itself. Addressed to the origin it spoke from. */
export function acknowledgeOpen(event: MessageEvent, request: OpenRequest): void {
    const view = event.source as Window | null;
    if (!view || !event.origin) return;
    try {
        view.postMessage({ type: OPEN_RESULT_MESSAGE_TYPE, id: request.id }, event.origin);
    } catch { /* the frame went away between asking and being answered */ }
}

/** Tell a frame where the address now says it is. Addressed to the origin the
 *  frame was loaded from: it is this app speaking, and a page the frame has
 *  since been navigated to should not hear where the Lemma's app is. */
export function tellLocation(view: Window, origin: string, path: string): void {
    try {
        view.postMessage({ type: LOCATION_MESSAGE_TYPE, path }, origin);
    } catch { /* the frame went away between the move and the telling */ }
}

/** An app's address with a path inside it: where its frame starts. */
export function appUrlAt(url: string, path: string): string {
    if (path === "/") return url;
    try {
        const next = new URL(url);
        next.pathname = next.pathname.replace(/\/$/, "") + path;
        return next.href;
    } catch {
        return url;
    }
}
