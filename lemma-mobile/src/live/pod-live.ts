import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { AppState } from "react-native";
import { currentServer, freshAccess, renewAccess } from "../session/auth";
import { LiveSocket, type LiveHandlers, type LiveStatus, type WebSocketLike } from "@web/live/socket";

/** The phone's half of the web's `live/pod-live.ts`: one live socket per
 *  Lemma (`/pods/{pod}/live`), shared by every screen that subscribes to it,
 *  carrying a group's messages, the inbox, a conversation's run and a table's
 *  rows. `socket.ts` is the web's own, unchanged; what differs is here:
 *
 *  - The token is the app's own (`freshAccess`), refreshed through its own
 *    session after a 4401, never a cookie.
 *  - The URL is built by hand: React Native's `URL` cannot set search params.
 *  - The token also goes in an `Authorization` header. The server takes a
 *    header first, then the `sAccessToken` cookie, then the URL; and on
 *    Android native requests share the WebView's cookie jar, where the
 *    sign-in page left a cookie session that nothing keeps fresh. Sent in the
 *    URL alone, the token lost to that cookie once it expired.
 *  - It wakes when the app comes back to the front, not on a browser tab.
 *
 *  Only signed in for real: sample data has no server to listen to, so
 *  `liveAvailable()` is false and the screens poll their sample source. */

/** React Native's WebSocket takes headers as a third argument, which the
 *  DOM's type does not know of. */
const NativeSocket = WebSocket as unknown as new (url: string, protocols: null, options: { headers: Record<string, string> } | null) => WebSocketLike;

const sockets = new Map<string, LiveSocket>();
let enabled = false;

/** The session says whether it is live; sample data and signed out are not. */
export function setLiveEnabled(next: boolean): void {
    if (enabled === next) return;
    enabled = next;
    if (!next) {
        for (const socket of sockets.values()) socket.close();
        sockets.clear();
    }
}

export function liveAvailable(): boolean {
    return enabled && currentServer() !== null;
}

function phoneLiveUrl(apiUrl: string, podId: string, token: string | null): string {
    const root = apiUrl.replace(/\/$/, "").replace(/^http(s?):\/\//, "ws$1://");
    return `${root}/pods/${encodeURIComponent(podId)}/live${token ? `?access_token=${encodeURIComponent(token)}` : ""}`;
}

function appWakeups(wake: () => void): () => void {
    const listening = AppState.addEventListener("change", (state) => {
        if (state === "active") wake();
    });
    return () => listening.remove();
}

export function podLive(podId: string): LiveSocket {
    const held = sockets.get(podId);
    if (held && held.status !== "closed") return held;
    const made = new LiveSocket({
        url: (token) => phoneLiveUrl(currentServer()?.api ?? "", podId, token),
        auth: {
            token: () => freshAccess(),
            refresh: async () => {
                if (!(await renewAccess())) throw new Error("Signed out.");
            },
        },
        open: (url) => {
            const token = /[?&]access_token=([^&]+)/.exec(url)?.[1];
            const headers = token ? { headers: { Authorization: `Bearer ${decodeURIComponent(token)}` } } : null;
            return new NativeSocket(url, null, headers);
        },
        wakeups: appWakeups,
    });
    sockets.set(podId, made);
    return made;
}

/** The Lemma's socket status, for screens that poll only while it is down.
 *  `"idle"` while nothing is subscribed. */
export function useLiveStatus(podId: string | null): LiveStatus {
    const subscribe = (notify: () => void) => {
        if (!podId || !liveAvailable()) return () => {};
        return podLive(podId).onStatus(notify);
    };
    const read = () => (podId && liveAvailable() ? podLive(podId).status : "closed");
    return useSyncExternalStore(subscribe, read, () => "idle");
}

/** How often a screen reads again over REST: never while the socket is open,
 *  `everyMs` while it is down, as the reconnect fallback. */
export function pollWhenDown(status: LiveStatus, everyMs: number): number | false {
    return status === "open" ? false : everyMs;
}

/** A subscription held for as long as the screen wants it. The handlers are
 *  read through a ref, so a re-render does not resubscribe; a change of topic
 *  or params does. `since` is read when the subscription is made. */
export function useLiveTopic(
    podId: string | null,
    topic: string,
    params: Record<string, unknown> | null,
    handlers: LiveHandlers,
    options: { since?: () => string | null; onCursor?: (cursor: string | null) => void } = {},
): void {
    const latest = useRef(handlers);
    latest.current = handlers;
    const extra = useRef(options);
    extra.current = options;
    const key = params ? JSON.stringify(params) : null;
    useEffect(() => {
        if (!podId || key === null || !liveAvailable()) return;
        const given = JSON.parse(key) as Record<string, unknown>;
        const keep = (cursor: string | null) => extra.current.onCursor?.(cursor);
        const subscription = podLive(podId).subscribe(topic, given, {
            onSubscribed: (cursor) => { keep(cursor); latest.current.onSubscribed?.(cursor); },
            onEvents: (events, cursor) => { keep(cursor); latest.current.onEvents?.(events, cursor); },
            onResync: (reason, cursor) => { keep(cursor); latest.current.onResync?.(reason, cursor); },
            onEnded: (reason, message) => latest.current.onEnded?.(reason, message),
        }, extra.current.since?.() ?? null);
        return () => subscription.close();
    }, [podId, topic, key]);
}

/** Something that lapses on its own: a typing note, shown for a few seconds
 *  after the last one heard. */
export function useLapsing<T>(lapseMs: number): [T[], (value: T, key: string) => void] {
    const [held, setHeld] = useState<Map<string, { value: T; until: number }>>(new Map());
    useEffect(() => {
        if (held.size === 0) return;
        const next = Math.min(...[...held.values()].map((entry) => entry.until)) - Date.now();
        const timer = setTimeout(() => setHeld((was) => {
            const now = Date.now();
            const kept = new Map([...was].filter(([, entry]) => entry.until > now));
            return kept.size === was.size ? was : kept;
        }), Math.max(50, next));
        return () => clearTimeout(timer);
    }, [held]);
    const add = (value: T, key: string) => setHeld((was) => new Map(was).set(key, { value, until: Date.now() + lapseMs }));
    return [[...held.values()].map((entry) => entry.value), add];
}
