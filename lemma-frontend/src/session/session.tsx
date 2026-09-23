"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { AuthState, LemmaClient } from "lemma-sdk";
import { useQueryClient } from "@tanstack/react-query";
import { lemma, hasApiUrl, hasToken } from "./client";
import { key } from "./storage";
import { askTheApi, type Person } from "./who";
import { PORTAL_PATH } from "@/auth/config";
import { MISSING_API_URL } from "./origins";
import { source } from "@/data";
import { sessionStatus, doorFor, type SessionStatus } from "./auth-state";
import { LemmaLogo } from "@/ui/icons";

/** Who is asking.
 *
 *  Before this, the answer was inferred from an unrelated query: the shell read
 *  `orgs.isError && /401|unauthor/` and, failing that, `orgs.data.length === 0`.
 *  The second half is the tell — somebody who is signed in and belongs to no
 *  organization is not unauthenticated, and showing them a box asking for a
 *  bearer token was the app confidently answering a question it had never asked.
 *
 *  The question has an endpoint. `AuthManager` calls `GET /users/me`, keeps the
 *  answer, and — this is the part worth knowing — the SDK's own HTTP client
 *  calls `markUnauthenticated()` on any 401 from any request. So a session that
 *  dies halfway through an afternoon turns this state over without anything
 *  here watching for it.
 */

export type { SessionStatus };

export interface Session {
    status: SessionStatus;
    user: Person | null;
    /** Hand off to the platform's auth portal, and come back here. */
    signIn: () => void;
    /** End it everywhere: the server, this browser, and this tab's memory. */
    signOut: () => Promise<void>;
}

/** The SDK's `useAuth`, with an off switch.
 *
 *  Not a gratuitous reimplementation — it is eight lines and it exists for the
 *  one thing the SDK's version cannot do, which is nothing. `useAuth` fires
 *  `checkAuth()` on mount unconditionally, and `checkAuth` initialises the
 *  SuperTokens browser SDK, which posts to `{api}/st/auth/session/refresh`.
 *  In sample mode there is no API to post to: the whole point of that mode is a
 *  backend that is not there, so the request is answered by a connection
 *  refused and the one screen you can look at without a session opens with an
 *  error in the console.
 *
 *  `enabled` never changes within a page — it comes from a module constant read
 *  once at load — so this does not move a hook between renders.
 */
function useAuthState(client: LemmaClient | null, enabled: boolean): AuthState {
    /* No client means no API origin to have made one against. The state stays
       `loading` and is never published, because `sessionStatus` answers
       `unconfigured` before it is ever consulted. */
    const [state, setState] = useState<AuthState>(() => client?.auth.getState() ?? { status: "loading", user: null });
    /* The direct ask is worth making once per load. Repeating it on every
       `unauthenticated` would turn a signed-out tab into a poller. */
    const asked = useRef(false);

    useEffect(() => {
        if (!enabled || !client) return;
        let live = true;
        const unsubscribe = client.auth.subscribe(next => {
            if (!live) return;
            if (next.status !== "unauthenticated" || asked.current) {
                setState(next);
                return;
            }
            asked.current = true;
            /* Hold the door shut rather than showing it and taking it away: a
               sign-in screen that flashes for the length of one request is
               worse than a moment longer on "Opening…". */
            setState({ status: "loading", user: null });
            void askTheApi().then(user => {
                if (!live) return;
                setState(user ? { status: "authenticated", user } : next);
            });
        });
        if (client.auth.getState().status === "loading") {
            /* `checkAuth` records its own failures in the state it publishes;
               there is nothing here to do with a rejection. */
            void client.auth.checkAuth().catch(() => undefined);
        }
        return () => { live = false; unsubscribe(); };
    }, [client, enabled]);

    return state;
}

export function useSession(): Session {
    const cache = useQueryClient();
    const sample = source.label === "sample";
    /* Read once, like `sample`: both come from module state and browser
       storage that this page cannot change under itself, so neither moves a
       hook between renders. */
    const configured = useMemo(() => hasApiUrl(), []);
    /* Built only when there is somewhere to point it. `LemmaClient` takes an
       origin as a required string, and there is no honest one to give it. */
    const client = useMemo(() => (configured ? lemma() : null), [configured]);
    const auth = useAuthState(client, !sample);

    const signIn = useCallback(() => {

        const here = window.location.pathname + window.location.search;
        window.location.assign(PORTAL_PATH + "?redirect_uri=" + encodeURIComponent(here));
    }, []);

    const signOut = useCallback(async () => {
        try {
            /* Revokes server-side and clears the SuperTokens frontend markers.
               The markers matter: while `sFrontToken` is present this browser
               reads itself as signed in, so a sign-out that only reached the
               network leaves the app bouncing somebody straight back into the
               workspace they were trying to leave. */
            await client?.auth.signOut();
        } catch {
            /* Best effort. A sign-out that could not reach the server must
               still put the person on the other side of the door. */
        }
        cache.clear();
        /* A full document load, deliberately: it is the only thing that drops
           every in-memory copy of what the last person could see — the query
           cache, the session hook, the mounted app frames. A soft navigation
           keeps all three. */
        window.location.assign("/");
    }, [client, cache]);

    return {
        status: sessionStatus(auth.status, sample, configured),
        user: auth.user as Session["user"],
        signIn,
        signOut,
    };
}

function Screen({ children }: { children: ReactNode }) {
    return (
        <div className="screen">
            <div className="screen__inner">{children}</div>
        </div>
    );
}

/** The door.
 *
 *  Deliberately not a form. This app does not own the password — the platform's
 *  auth portal does — and a box that looks like it takes one, then bounces the
 *  person somewhere else to type it again, is worse than a button that says
 *  where it is going.
 */
/* One trip per tab, recorded before it is taken. `sessionStorage` rather than
   `localStorage`: the question is "has this tab just been to the portal", which
   dies with the tab and must not be inherited by the next one. */
const SENT = key("sent-to-portal");
const sent = {
    mark() { try { sessionStorage.setItem(SENT, "1"); } catch { /* no storage */ } },
    was() { try { return sessionStorage.getItem(SENT) === "1"; } catch { return false; } },
    clear() { try { sessionStorage.removeItem(SENT); } catch { /* no storage */ } },
};

/** Taken to the door, rather than shown a picture of one. */
function ToThePortal({ signIn }: { signIn: () => void }) {
    useEffect(() => {
        sent.mark();
        signIn();
    }, [signIn]);
    /* Named, because this paints for the length of one navigation and an
       unlabelled blank is indistinguishable from a broken page. */
    return (
        <Screen>
            <p className="screen__mark"><LemmaLogo /></p>
            <p role="status">Taking you to sign in…</p>
        </Screen>
    );
}

/** Sent to the portal, came back, still signed out.
 *
 *  The end of the loop guard. Something is wrong that another trip will not
 *  fix — most likely a session this origin cannot see — so it says so and lets
 *  a person choose, instead of setting off again.
 */
function StalledScreen({ signIn }: { signIn: () => void }) {
    return (
        <Screen>
            <p className="screen__mark"><LemmaLogo /></p>
            <h2>You are still signed out</h2>
            <p>
                You have just been to the sign-in page and come back without a session. Trying
                once more is worth it; if it keeps happening, this browser is not holding on to
                the session cookie.
            </p>
            <div className="screen__actions">
                <button className="btn btn--primary" onClick={() => { sent.clear(); signIn(); }}>
                    Try again
                </button>
            </div>
        </Screen>
    );
}

/** A bearer token in this browser that the API will not accept.
 *
 *  Kept as a screen because the portal cannot fix it — the credential is local,
 *  and the only way out is to replace or remove it.
 */
function TokenScreen() {
    return (
        <Screen>
            <p className="screen__mark"><LemmaLogo /></p>
            <h2>This browser is holding a token the API refused</h2>
            <p>
                Signing in again will not clear it, because it is kept here rather than by the
                server. <a href="/connect">Replace or remove it</a>.
            </p>
        </Screen>
    );
}

/** Nothing has been configured yet.
 *
 *  Not an error screen and not the door. Both of those describe something
 *  that went wrong between this app and a backend, and there is no backend
 *  here to have gone wrong with — somebody has cloned this and run it, which
 *  is the expected first five minutes. So it says the one thing that is true,
 *  names the variable, and points at the two ways forward: configure it, or
 *  look at the app without one.
 */
export function SetupScreen() {
    return (
        <Screen>
            <p className="screen__mark">
                <LemmaLogo />
            </p>
            <h2>Point this at an API</h2>
            <p>{MISSING_API_URL}</p>
            <p className="screen__aside">
                To look around without a backend at all, set <code>NEXT_PUBLIC_DATA=sample</code> — every
                screen is clickable and nothing is real.
            </p>
            {/* The other way in, for a deployment whose origin is decided at
                run time rather than at build time: `/connect` writes the same
                value into this browser and is reachable without any of this. */}
            <p className="screen__footnote">
                Or set the address in this browser only, from <a href="/connect">/connect</a>.
            </p>
        </Screen>
    );
}

/** Everything inside this has somebody to show it to.
 *
 *  Sample mode passes straight through: it has no backend to be authenticated
 *  against, and gating it would leave nothing to look at without a session —
 *  which is the one thing it exists for.
 */
export function SessionGate({ children }: { children: ReactNode }) {
    const session = useSession();

    /* In an effect, not in the branch below: clearing it is a side effect, and
       a render is not allowed to have one. The next sign-out starts a fresh
       trip either way. */
    const through = session.status === "in" || session.status === "sample";
    useEffect(() => { if (through) sent.clear(); }, [through]);

    if (session.status === "unconfigured") {
        return <SetupScreen />;
    }

    if (session.status === "loading") {
        return (
            <Screen>
                <p role="status">Opening…</p>
            </Screen>
        );
    }

    if (session.status === "out") {
        const door = doorFor(hasToken(), sent.was());
        if (door === "token") return <TokenScreen />;
        if (door === "stalled") return <StalledScreen signIn={session.signIn} />;
        return <ToThePortal signIn={session.signIn} />;
    }

    return <>{children}</>;
}
