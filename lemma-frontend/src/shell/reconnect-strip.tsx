"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiUrl, hasApiUrl } from "@/session/client";
import { source } from "@/data";
import { LoadingIndicator } from "@/ui/loading";
import {
    CONNECTED,
    RECONNECTED_EVENT,
    afterProbe,
    healthAnswered,
    isTransportFailure,
    probeDelay,
    suspect,
    type Connection,
} from "./connection";

async function probe(): Promise<boolean> {
    try {
        const response = await fetch(apiUrl().replace(/\/$/, "") + "/health/live", {
            credentials: "omit",
            cache: "no-store",
        });
        return healthAnswered(response.status);
    } catch {
        return false;
    }
}

/** "Lemma is restarting… reconnecting", while the API does not answer.
 *
 *  Saving Server setup restarts a local server under the page, and a hosted
 *  one restarts on deploy. Every query in flight then fails, and without this
 *  the screen showed a scatter of unrelated errors that stayed until reload.
 *  Here one line says what is happening; when the server answers again every
 *  query is refetched and `RECONNECTED_EVENT` tells views holding state of
 *  their own (a conversation's live run) to reload it. The decisions live in
 *  `connection.ts`. */
export function ReconnectStrip() {
    const cache = useQueryClient();
    const [connection, setConnection] = useState<Connection>(CONNECTED);
    const current = useRef(connection);
    current.current = connection;
    const enabled = source.label !== "sample" && hasApiUrl();

    /* Start looking only when something failed in transport: nothing polls
       while things work. */
    useEffect(() => {
        if (!enabled) return;
        const look = () => setConnection(was => suspect(was));
        const unsubscribe = cache.getQueryCache().subscribe(event => {
            if (event.type !== "updated") return;
            const { state } = event.query;
            if (isTransportFailure(state.fetchFailureReason ?? state.error)) look();
        });
        window.addEventListener("offline", look);
        window.addEventListener("online", look);
        return () => {
            unsubscribe();
            window.removeEventListener("offline", look);
            window.removeEventListener("online", look);
        };
    }, [cache, enabled]);

    useEffect(() => {
        if (!enabled || connection.phase === "up") return;
        let cancelled = false;
        const timer = setTimeout(async () => {
            const answered = await probe();
            if (cancelled) return;
            const { next, recovered } = afterProbe(current.current, answered);
            setConnection(next);
            if (recovered) {
                void cache.invalidateQueries();
                window.dispatchEvent(new Event(RECONNECTED_EVENT));
            }
        }, probeDelay(connection));
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [cache, connection, enabled]);

    if (connection.phase !== "down") return null;
    return (
        <div className="reconnect-strip" role="status" aria-live="polite">
            <span aria-hidden="true"><LoadingIndicator inline label="Reconnecting" /></span>
            <span>Lemma is restarting… reconnecting</span>
        </div>
    );
}
