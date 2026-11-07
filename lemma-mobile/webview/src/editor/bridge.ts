import type { CallMethod, CarriedBlob, FromPage, ToPage } from "../../../src/areas/files/web/protocol";

/** The page's half of the bridge to the phone app.
 *
 *  Out: `window.ReactNativeWebView.postMessage`, a JSON string. In: the app
 *  calls `window.__fromApp(message)` with `injectJavaScript`. A `call` is
 *  answered by a `reply` carrying the same id; an error keeps its HTTP status,
 *  so the web's code can tell a 404 from a 403 as it does on the web. */

declare global {
    interface Window {
        ReactNativeWebView?: { postMessage: (data: string) => void };
        __fromApp?: (message: ToPage) => void;
    }
}

export function post(message: FromPage): void {
    window.ReactNativeWebView?.postMessage(JSON.stringify(message));
}

type Listener = (message: ToPage) => void;
const listeners = new Set<Listener>();

export function listen(listener: Listener): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
}

/** An error as the SDK throws one: a message and the status it came with. */
export class CallError extends Error {
    statusCode: number | undefined;
    constructor(message: string, statusCode: number | null) {
        super(message);
        this.statusCode = statusCode ?? undefined;
    }
}

let next = 1;
const waiting = new Map<number, { resolve: (value: unknown) => void; reject: (error: unknown) => void }>();

window.__fromApp = (message: ToPage) => {
    if (message.type === "reply") {
        const held = waiting.get(message.id);
        if (!held) return;
        waiting.delete(message.id);
        if (message.ok) held.resolve(message.value);
        else held.reject(new CallError(message.message, message.statusCode));
        return;
    }
    for (const listener of listeners) listener(message);
};

/** How many calls are still out. A write handed over is the app's from then on. */
export function pending(): number {
    return waiting.size;
}

function base64Of(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ""));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
    });
}

/** Blobs cannot cross as JSON, so they go as base64 with their type and name. */
async function carried(value: unknown): Promise<unknown> {
    if (value instanceof Blob) {
        const out: CarriedBlob = { __blob: await base64Of(value), type: value.type, name: value instanceof File ? value.name : null };
        return out;
    }
    if (Array.isArray(value)) return Promise.all(value.map(carried));
    if (value && typeof value === "object") {
        const entries = await Promise.all(Object.entries(value).map(async ([key, one]) => [key, await carried(one)] as const));
        return Object.fromEntries(entries);
    }
    return value;
}

export async function call<T>(method: CallMethod, ...args: unknown[]): Promise<T> {
    const id = next++;
    const sent = await carried(args) as unknown[];
    return new Promise<T>((resolve, reject) => {
        waiting.set(id, { resolve: resolve as (value: unknown) => void, reject });
        post({ type: "call", id, method, args: sent });
    });
}
