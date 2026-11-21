"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { apiUrl, hasApiUrl } from "@/session/client";
import { source } from "@/data";
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

/** How long "Back" stays once the page has been read again. */
const BACK_MS = 2_000;

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

/** Whether something on screen still failed for want of an answer after it
 *  was all read again: then the page is not up to date, and is not said to
 *  be. */
function stillUnanswered(cache: QueryClient): boolean {
    return cache.getQueryCache().findAll({ type: "active" })
        .some((query) => query.state.status === "error" && isTransportFailure(query.state.error));
}

/** The connection, said in one pill at the top of the page (design.md,
 *  "States").
 *
 *  Offline: the browser says it has no network, and what is on screen
 *  stays. Can't reach Lemma: the network is there but the API does not
 *  answer -- a deploy, a local server restarted from Server setup, a gateway
 *  down -- and the pill counts down to the next real probe, with Try now.
 *  Back: the server answered again, every query was read again, and only
 *  once that finished does it say the page is up to date, for a moment.
 *  `RECONNECTED_EVENT` tells views holding state of their own (a
 *  conversation's live run) to reload it. The decisions about when to look
 *  and how long to wait live in `connection.ts`; nothing polls while things
 *  work. */
export function ReconnectStrip() {
    const cache = useQueryClient();
    const [connection, setConnection] = useState<Connection>(CONNECTED);
    const current = useRef(connection);
    current.current = connection;
    const enabled = source.label !== "sample" && hasApiUrl();
    /* The browser's own word on its network. */
    const [offline, setOffline] = useState(false);
    /* Something may have been missed -- the browser was offline, or the
       server did not answer -- so the recovery reads everything again. */
    const missed = useRef(false);
    /* When the next probe goes, for the countdown; null while one is out. */
    const [nextAt, setNextAt] = useState<number | null>(null);
    const [now, setNow] = useState(0);
    /* A Try now press: the next probe goes at once. */
    const [asked, setAsked] = useState(0);
    const hurry = useRef(false);
    const [back, setBack] = useState(false);

    /* Start looking only when something failed in transport, or the browser
       went offline and back. */
    useEffect(() => {
        if (!enabled) return;
        const look = () => setConnection((was) => suspect(was));
        const unsubscribe = cache.getQueryCache().subscribe((event) => {
            if (event.type !== "updated") return;
            const { state } = event.query;
            if (isTransportFailure(state.fetchFailureReason ?? state.error)) look();
        });
        const wentOffline = () => {
            missed.current = true;
            setBack(false);
            setOffline(true);
        };
        const cameBack = () => {
            setOffline(false);
            look();
        };
        if (typeof navigator !== "undefined" && navigator.onLine === false) wentOffline();
        window.addEventListener("offline", wentOffline);
        window.addEventListener("online", cameBack);
        return () => {
            unsubscribe();
            window.removeEventListener("offline", wentOffline);
            window.removeEventListener("online", cameBack);
        };
    }, [cache, enabled]);

    /* Probe on the backoff while the server is in doubt; not while offline,
       when no probe could answer. */
    useEffect(() => {
        if (!enabled || offline || connection.phase === "up") {
            setNextAt(null);
            return;
        }
        if (connection.phase === "down") missed.current = true;
        let cancelled = false;
        const delay = hurry.current ? 0 : probeDelay(connection);
        hurry.current = false;
        setNow(Date.now());
        setNextAt(Date.now() + delay);
        const timer = setTimeout(async () => {
            setNextAt(null);
            const answered = await probe();
            if (cancelled) return;
            const { next } = afterProbe(current.current, answered);
            setConnection(next);
            if (!answered || !missed.current) return;
            missed.current = false;
            window.dispatchEvent(new Event(RECONNECTED_EVENT));
            await cache.invalidateQueries();
            if (!stillUnanswered(cache)) setBack(true);
        }, delay);
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [cache, connection, enabled, offline, asked]);

    /* The countdown, a second at a time, only while it is shown. */
    useEffect(() => {
        if (nextAt === null) return;
        const timer = setInterval(() => setNow(Date.now()), 250);
        return () => clearInterval(timer);
    }, [nextAt]);

    useEffect(() => {
        if (!back) return;
        const timer = setTimeout(() => setBack(false), BACK_MS);
        return () => clearTimeout(timer);
    }, [back]);

    if (!enabled) return null;
    if (offline) {
        return (
            <div className="reconnect-strip" role="status" aria-live="polite">
                <i aria-hidden="true" />
                <span>You’re offline. What’s on screen stays; nothing new loads or sends until you’re back.</span>
            </div>
        );
    }
    if (connection.phase === "down") {
        const seconds = nextAt === null ? null : Math.max(1, Math.ceil((nextAt - now) / 1000));
        return (
            <div className="reconnect-strip" role="status" aria-live="polite">
                <i aria-hidden="true" />
                <span>
                    Can’t reach Lemma.{" "}
                    {seconds === null ? "Trying again…" : <>Trying again in <span className="reconnect-strip__n">{seconds}</span> s</>}
                </span>
                <button
                    type="button"
                    className="b b--s b--secondary"
                    disabled={nextAt === null}
                    onClick={() => {
                        hurry.current = true;
                        setAsked((n) => n + 1);
                    }}
                >
                    Try now
                </button>
            </div>
        );
    }
    if (back) {
        return (
            <div className="reconnect-strip reconnect-strip--ok" role="status" aria-live="polite">
                <i aria-hidden="true" />
                <span>Back. Everything on this page is up to date.</span>
            </div>
        );
    }
    return null;
}
