"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { AuthState, LemmaClient } from "lemma-sdk";
import { onlineManager, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Modal } from "@/shell/modal";
import { lemma, hasApiUrl, hasToken } from "./client";
import { retainWorkspaceOwner, sessionStorageChanged } from "./storage";
import { portalTrip as sent } from "./portal-trip";
import { observeAuth } from "./observe-auth";
import { PageLoading } from "@/ui/loading";
import { resetAnalyticsIdentity } from '@/site/analytics/client';
import { askTheApi, type Person } from "./who";
import { PORTAL_PATH } from "@/auth/config";
import { MISSING_API_URL } from "./origins";
import { source } from "@/data";
import { sessionStatus, doorFor, unreachableRetryDelay, type SessionStatus } from "./auth-state";
import { LemmaLogo } from "@/ui/icons";
import { signedOutOfThisComputer } from "@/desktop/auto-connect";
import { openedTranscripts } from "@/thread/opened-transcripts";
import { forgetOnDevice } from "./device-cache";

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
    /** After the account itself is gone: forget it in this browser and this
     *  tab, then land on `destination`. Unlike `signOut` this never refuses —
     *  the server has already ended the session, so there is nothing to
     *  confirm, and a browser left believing the person is here is worse. */
    leave: (destination: string) => Promise<void>;
    /** Ask about the session again, after the API did not answer. */
    retry: () => void;
    /** Whether the API answers at all -- its liveness probe, not a session
     *  check, so waiting on it cannot spend the refresh budget. */
    reachable: () => Promise<boolean>;
}

/** Who this page has shown, once it has shown anybody. Page-wide, because
 *  every `useSession` watches the session for itself, and one mounted after
 *  the session ended must not take the ended session for nobody having been
 *  here -- which would forget what is still on screen. */
let shown: string | undefined;

/** Whether this page is on its way out on purpose: signing out, or leaving
 *  after the account went. The session ends under it then too, and that is no
 *  surprise to tell anybody about. */
let leaving = false;

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
    const cache = useQueryClient();
    /* The query cache, the transcripts the conversation pane keeps beside
       it (`opened-transcripts.ts`) and what the device kept between visits
       (`device-cache.ts`): all of it is what the last person could see. */
    const forgetEverything = () => {
        cache.clear();
        openedTranscripts.clear();
        void forgetOnDevice();
    };
    const [state, setState] = useState<AuthState>({ status: "loading", user: null });
    useEffect(() => {
        if (!enabled || !client) return;
        shown ??= client.auth.getState().user?.id;
        const receive = (next: AuthState) => {
            if (next.status !== "loading") {
                const user = next.user?.id;
                /* A session that ends under a page somebody is using -- a 401
                   mid-afternoon -- is not somebody else arriving. What they
                   were reading stays on screen behind "You've been signed
                   out", so nothing is forgotten yet: whoever signs in next is
                   asked about below, and a different person there, or in
                   another tab (the owner key changes, and every tab clears
                   and reloads), wipes it all before anything is drawn. Signing
                   out on purpose forgets everything itself (`forgetHere`). */
                const ended = Boolean(shown) && next.status !== "authenticated";
                if (!ended) {
                    if (shown && shown !== user) forgetEverything();
                    try {
                        if (retainWorkspaceOwner(localStorage, user ?? null)) forgetEverything();
                    } catch { /* Storage may be unavailable; the in-memory boundary still applies. */ }
                }
                if (next.status === "authenticated") shown = user;
                /* The session check is `GET /users/me`, the same answer
                   `["current-user"]` asks for. Handed over here, the shell,
                   the profile and the arrival screen read it rather than
                   asking again the moment the gate opens. */
                if (next.status === "authenticated" && next.user) cache.setQueryData(["current-user"], next.user);
            }
            setState(next);
        };
        /* Ended under this page already, and this is something mounted
           since -- the next page of the frame, a dialog. Discovery would ask
           the API again, and could find a session the page has let go of:
           it is told the state as it is instead. */
        if (shown && client.auth.getState().status === "unauthenticated") {
            receive(client.auth.getState());
            return client.auth.subscribe(receive);
        }
        return observeAuth(client.auth, askTheApi, receive);
    }, [client, enabled, cache]);

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
        sent.mark();
        const here = window.location.pathname + window.location.search + window.location.hash;
        window.location.assign(PORTAL_PATH + "?redirect_uri=" + encodeURIComponent(here));
    }, []);

    /* What leaving means on this side of the wire, whatever the server said. */
    const forgetHere = useCallback(async (destination: string) => {
        resetAnalyticsIdentity();
        /* This computer's pairing of the person leaving stops taking work,
           so the next person at this Mac is not running theirs. */
        await signedOutOfThisComputer();
        cache.clear();
        sent.clear();
        try { retainWorkspaceOwner(localStorage, null); } catch { /* no storage */ }
        /* Waited for, unlike the rest: the page is about to go, and a wipe
           still in flight could go with it. */
        await forgetOnDevice();
        /* A full document load, deliberately: it is the only thing that drops
           every in-memory copy of what the last person could see — the query
           cache, the session hook, the mounted app frames. A soft navigation
           keeps all three. */
        window.location.assign(destination);
    }, [cache]);

    const signOut = useCallback(async () => {
        leaving = true;
        try {
            if (client && !(await client.auth.signOut())) {
                throw new Error("We couldn’t confirm sign-out. Check your connection and try again.");
            }
        } catch (error) {
            leaving = false;
            throw error;
        }
        await forgetHere("/");
    }, [client, forgetHere]);

    const leave = useCallback(async (destination: string) => {
        leaving = true;
        /* Still asked, for the SDK's own markers; its answer changes nothing. */
        if (client) await client.auth.signOut().catch(() => false);
        await forgetHere(destination);
    }, [client, forgetHere]);

    const retry = useCallback(() => {
        if (client) void client.auth.checkAuth().catch(() => undefined);
    }, [client]);
    const reachable = useCallback(
        async () => (client ? client.auth.isReachable() : false),
        [client],
    );

    return {
        status: sessionStatus(auth.status, sample, configured),
        user: auth.user as Session["user"],
        signIn,
        signOut,
        leave,
        retry,
        reachable,
    };
}

function Screen({ children }: { children: ReactNode }) {
    return (
        <div className="screen">
            <div className="screen__inner">{children}</div>
        </div>
    );
}

/** Taken to the door, rather than shown a picture of one. */
function ToThePortal({ signIn, screen, ended = false }: { signIn: () => void; screen?: ReactNode; ended?: boolean }) {
    useEffect(() => {
        signIn();
    }, [signIn]);
    if (screen) return screen;
    /* Named, because this paints for the length of one navigation and an
       unlabelled blank is indistinguishable from a broken page. A session
       that ended under the page says why it is going. */
    return (
        <Screen>
            <p className="screen__mark"><LemmaLogo /></p>
            <p role="status">{ended ? "You’ve been signed out. Taking you to sign in…" : "Taking you to sign in…"}</p>
        </Screen>
    );
}

/** Where a wait on the API stands: the seconds to its next look, or null
 *  while it is looking; and the way to look now. */
export interface Retrying {
    seconds: number | null;
    tryNow: () => void;
}

/** The API did not answer, which is not the same as not knowing you.
 *
 *  Before this, a server restarting under the page -- saving Server setup does
 *  it -- came back as "signed out", and the page left for the sign-in portal.
 *  Now only a 401 does that. Here the page waits: it asks the API's liveness
 *  probe on a backoff and checks the session again the moment it answers, and
 *  "Try now" does that at once. The probe, not the session check, is what
 *  repeats: every session check can cost a refresh, and a page that spent
 *  them on an outage would trip the refresh breaker and sign itself out.
 *
 *  The countdown is the back-off's own: the seconds shown are the seconds
 *  until the probe runs. */
function useRetrying(retry: () => void, reachable: () => Promise<boolean>): Retrying {
    const [attempt, setAttempt] = useState(0);
    const [due, setDue] = useState(() => Date.now() + unreachableRetryDelay(0));
    const [looking, setLooking] = useState(false);
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        let cancelled = false;
        /* `due` was set in the same step as `attempt`, so this is the wait
           the countdown is showing. */
        const timer = setTimeout(async () => {
            if (cancelled) return;
            setLooking(true);
            const answered = await reachable();
            if (cancelled) return;
            if (answered) {
                retry();
                return;
            }
            setLooking(false);
            setDue(Date.now() + unreachableRetryDelay(attempt + 1));
            setAttempt(attempt + 1);
        }, Math.max(0, due - Date.now()));
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
        /* Not on `due`: it moves only with `attempt`. */
    }, [attempt, reachable, retry]);
    useEffect(() => {
        const tick = setInterval(() => setNow(Date.now()), 250);
        return () => clearInterval(tick);
    }, []);
    return {
        seconds: looking ? null : Math.max(1, Math.ceil((due - now) / 1000)),
        tryNow: retry,
    };
}

function Unreachable({ retry, reachable, render }: {
    retry: () => void;
    reachable: () => Promise<boolean>;
    render?: (retrying: Retrying) => ReactNode;
}) {
    const retrying = useRetrying(retry, reachable);
    if (render) return render(retrying);
    return (
        <Screen>
            <p className="screen__mark"><LemmaLogo /></p>
            <h2 role="alert">Can’t reach Lemma right now</h2>
            <p>
                Its server isn’t answering, so nothing can load yet.{" "}
                {retrying.seconds === null ? "Trying again now…" : `Trying again in ${retrying.seconds} s.`}
            </p>
            <div className="screen__actions">
                <button className="btn btn--primary" onClick={retrying.tryNow}>
                    Try now
                </button>
            </div>
        </Screen>
    );
}

/** The session ended under a page somebody was using.
 *
 *  The page stays -- it is what they were reading, and it is theirs -- with
 *  this over it. Nothing behind it fetches any more (`holdTheNetwork`), and
 *  signing in again comes back to the same address. Put away, it leaves a
 *  small pill at the top of the page that does the same. */
function SignedOut({ email, signIn, otherAccount }: { email: string | null; signIn: () => void; otherAccount: () => void }) {
    const [open, setOpen] = useState(true);
    if (!open) {
        return (
            <div className="kout-pill" role="status">
                <i aria-hidden="true" />
                <span>Signed out</span>
                <button type="button" className="b b--s b--primary" onClick={signIn}>Sign in again</button>
            </div>
        );
    }
    return (
        <Modal
            narrow
            title="You’ve been signed out"
            subtitle="Your session ended. What’s on screen stays, but nothing new loads or saves until you sign in again. You’ll come back to this page."
            onClose={() => setOpen(false)}
            footer={(
                <>
                    <button type="button" className="b b--l b--primary" onClick={signIn}>Sign in again</button>
                    <p className="kout__note">
                        {email ? <>Last signed in as {email} · </> : null}
                        <button type="button" className="kout__link" onClick={otherAccount}>Use another account</button>
                    </p>
                </>
            )}
        >
            {null}
        </Modal>
    );
}

/** Whether this page has stopped its queries for a session that ended. */
let held = false;

/** Nothing more from the API, once the session has ended under the page.
 *
 *  Every query and mutation in the app goes through the query client, which
 *  fetches only while its online manager says so: told "offline" -- and no
 *  longer listening to the browser, which would say "online" again -- nothing
 *  refetches, nothing polls, nothing new loads and nothing saves; each waits,
 *  paused, as it would without a network. What was in flight is called off
 *  and keeps its last answer, so the page stays as it was. A 401 answered
 *  over and over is how a page trips the refresh breaker, and how a session
 *  that ended keeps knocking. */
function holdTheNetwork(cache: QueryClient) {
    if (held) return;
    held = true;
    onlineManager.setEventListener(() => undefined);
    onlineManager.setOnline(false);
    void cache.cancelQueries();
}

/** The session came back in this page: queries go on as before. */
function releaseTheNetwork() {
    if (!held) return;
    held = false;
    onlineManager.setEventListener((setOnline) => {
        const on = () => setOnline(true);
        const off = () => setOnline(false);
        window.addEventListener("online", on);
        window.addEventListener("offline", off);
        return () => {
            window.removeEventListener("online", on);
            window.removeEventListener("offline", off);
        };
    });
    onlineManager.setOnline(navigator.onLine !== false);
}

/** Sent to the portal, came back, still signed out.
 *
 *  Stop automatic redirects without assuming why session verification failed.
 */
function StalledScreen({ signIn }: { signIn: () => void }) {
    return (
        <Screen>
            <p className="screen__mark"><LemmaLogo /></p>
            <h2>We couldn’t finish signing you in</h2>
            <p>
                We couldn’t verify your session. Sign in again to continue, or return to the home page.
            </p>
            <div className="screen__actions">
                <button className="btn btn--primary" onClick={signIn}>
                    Sign in again
                </button>
                <a className="btn" href="/">Back to home</a>
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
export function SessionGate({ children, starting, unreachable, staysOnSignOut = false }: {
    children: ReactNode;
    /** Drawn while the session is asked about, and on the way to sign in;
     *  `words` says which ("Checking you're signed in"). */
    starting?: (words: string) => ReactNode;
    /** Drawn while the API is not answering, with where its next try stands. */
    unreachable?: (retrying: Retrying) => ReactNode;
    /** When the session ends under the page, keep the page and say so over it,
     *  rather than going to sign in. Only a 401 does this
     *  (`auth-state.ts`, `sessionStatus`). */
    staysOnSignOut?: boolean;
}) {
    const session = useSession();
    const cache = useQueryClient();
    /* Who this gate has let in, kept past the end of their session: the page
       they were using stays theirs until somebody signs in again. Written
       while drawing, and only ever to the person the session just named. */
    const last = useRef<Person | null>(null);
    if (session.status === "in" && session.user) last.current = session.user;
    const door = session.status === "out" ? doorFor(hasToken(), sent.recent()) : null;
    /* A token the API refused is not fixed by signing in again, so it keeps
       its own screen even mid-use. */
    const ended = staysOnSignOut && session.status === "out" && last.current !== null && door !== "token";

    useEffect(() => {
        if (ended) holdTheNetwork(cache);
        else if (session.status === "in") releaseTheNetwork();
    }, [ended, session.status, cache]);

    useEffect(() => {
        if (source.label === "sample") return;
        const changed = (event: StorageEvent) => {
            if (event.storageArea !== localStorage) return;
            if (!sessionStorageChanged(event.key, event.oldValue, event.newValue)) return;
            cache.clear();
            window.location.reload();
        };
        window.addEventListener("storage", changed);
        return () => window.removeEventListener("storage", changed);
    }, [cache]);

    if (session.status === "unconfigured") {
        return <SetupScreen />;
    }

    /* The page somebody is using stays on screen while the session is asked
       about again, and while it is on its way out on purpose. Drawn with the
       same tree whatever happens, so that nothing under it is mounted again. */
    const person = last.current;
    const keep = person !== null && (
        session.status === "in"
        || session.status === "loading"
        || (session.status === "out" && (ended || leaving))
    );
    if (keep) {
        const otherAccount = () => void session.leave(window.location.pathname + window.location.search + window.location.hash);
        return (
            <>
                <Fragment key={person.id}>{children}</Fragment>
                {ended && !leaving && <SignedOut email={person.email || null} signIn={session.signIn} otherAccount={otherAccount} />}
            </>
        );
    }

    if (session.status === "loading") {
        const words = "Checking you’re signed in";
        return starting ? starting(words) : <PageLoading label={words} />;
    }

    if (session.status === "unreachable") {
        return <Unreachable retry={session.retry} reachable={session.reachable} render={unreachable} />;
    }

    if (session.status === "out") {
        if (door === "token") return <TokenScreen />;
        if (door === "stalled") return <StalledScreen signIn={session.signIn} />;
        return <ToThePortal signIn={session.signIn} screen={starting?.("Taking you to sign in")} ended={person !== null} />;
    }

    return (
        <>
            <Fragment key={session.user?.id ?? session.status}>{children}</Fragment>
        </>
    );
}
