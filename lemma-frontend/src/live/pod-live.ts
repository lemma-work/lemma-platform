"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { source } from "@/data";
import { isLandingPreview } from "@/marketing/preview-mode";
import { apiUrl, hasApiUrl, lemma } from "@/session/client";
import { LiveSocket, liveUrl, type LiveHandlers, type LiveStatus, type WebSocketLike } from "./socket";

/** Every view in the app shares one live socket per pod: the first
 *  subscription opens it, the last one leaving lets it close. A socket that
 *  was refused for good (signed out, no access) is made again the next time
 *  something asks, so signing back in brings the live views back. */

const sockets = new Map<string, LiveSocket>();

/** Whether this session can hold a live socket at all: a configured API, and
 *  not the sample source or the landing page's tour. */
export function liveAvailable(): boolean {
    return !isLandingPreview() && source.label !== "sample" && hasApiUrl();
}

/** Back online, or the tab in front again: the moments a socket that died
 *  quietly is worth noticing at once rather than at its next ping. */
function browserWakeups(wake: () => void): () => void {
    if (typeof window === "undefined" || typeof document === "undefined") return () => {};
    const visible = () => { if (document.visibilityState === "visible") wake(); };
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", visible);
    return () => {
        window.removeEventListener("online", wake);
        document.removeEventListener("visibilitychange", visible);
    };
}

export function podLive(podId: string): LiveSocket {
    const held = sockets.get(podId);
    if (held && held.status !== "closed") return held;
    const client = lemma(podId);
    const made = new LiveSocket({
        url: (token) => liveUrl(apiUrl(), podId, token),
        auth: {
            /* A cookie session has no token to hand over, and needs none:
               the cookie rides the upgrade. */
            token: async () => {
                try {
                    return (await client.auth.getAccessToken()) || null;
                } catch {
                    return null;
                }
            },
            refresh: async () => { await client.auth.refreshAccessToken(); },
        },
        open: (url) => new WebSocket(url) as unknown as WebSocketLike,
        wakeups: browserWakeups,
    });
    sockets.set(podId, made);
    return made;
}

/** The pod's socket status, for the views that poll only while it is down.
 *  `"idle"` while nothing is subscribed, which no view polls on: a view that
 *  asks has subscribed already. */
export function useLiveStatus(podId: string | null): LiveStatus {
    const subscribe = (notify: () => void) => {
        if (!podId || !liveAvailable()) return () => {};
        return podLive(podId).onStatus(notify);
    };
    const read = () => (podId && liveAvailable() ? podLive(podId).status : "closed");
    return useSyncExternalStore(subscribe, read, () => "idle");
}

/** How often a view reads again over REST: never while the socket is open,
 *  `everyMs` while it is down -- the reconnect fallback, not the way the view
 *  stays fresh. */
export function pollWhenDown(status: LiveStatus, everyMs: number): number | false {
    return status === "open" ? false : everyMs;
}

/** A subscription held for as long as the component wants it. The handlers
 *  are read through a ref, so a re-render does not resubscribe; a change of
 *  topic or params does. `since` is read when the subscription is made. */
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
