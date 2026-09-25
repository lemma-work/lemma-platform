"use client";

import { useEffect, useRef, useState } from "react";
import { Screen } from "@/auth/screens";
import { onApi, PORTAL_PATH, siteOrigin } from "@/auth/config";
import { landing } from "@/auth/redirects";
import { Session } from "@/auth/supertokens";
import { LoadingIndicator } from "@/ui/loading";
import {
    appReturnUrl,
    awaitSession,
    completeRequest,
    dropPending,
    dropRequestId,
    handoffCode,
    heldRequestId,
    startHandoff,
    type PendingHandoff,
} from "./auth-handoff";

/** The app's side of a hosted sign-in: open the request, hand the person to
 *  their browser, wait. `auth-handoff.ts` has the whole exchange. */
export function DesktopSignIn({ mode }: { mode: "in" | "up" }) {
    const [pending, setPending] = useState<PendingHandoff | null>(null);
    const [code, setCode] = useState<string | null>(null);
    const [said, setSaid] = useState<string | null>(null);
    /* The browser is opened once per request on its own; after that only when
       asked. A reload of this page resumes the request without a second tab. */
    const opened = useRef<string | null>(null);

    const openBrowser = (handoff: PendingHandoff, force = false) => {
        if (!force && opened.current === handoff.requestId) return;
        opened.current = handoff.requestId;
        /* The shell cancels this navigation and opens it in the system browser
           — the `desktop_browser` marker is what it recognises. */
        window.location.assign(handoff.browserUrl);
    };

    useEffect(() => {
        let cancelled = false;
        void (async () => {
            try {
                const handoff = await startHandoff(siteOrigin(), PORTAL_PATH, mode === "up");
                if (cancelled) return;
                setPending(handoff);
                setCode(await handoffCode(handoff.requestId));
                openBrowser(handoff);
                const outcome = await awaitSession(handoff, () => cancelled);
                if (outcome === "signed-in") window.location.replace(landing(window.location.search));
            } catch (problem) {
                dropPending();
                if (!cancelled) setSaid(problem instanceof Error ? problem.message : "Sign-in could not be finished.");
            }
        })();
        return () => {
            cancelled = true;
        };
        // One request per mount; `mode` does not change under a mounted screen.
    }, []);

    if (said) {
        return (
            <Screen title="Let’s try that again" lead={said}>
                <div className="screen__actions">
                    <button className="btn btn--primary" onClick={() => { dropPending(); window.location.reload(); }}>
                        Start again
                    </button>
                </div>
            </Screen>
        );
    }

    return (
        <Screen
            title="Sign in with your browser"
            lead="Your browser handles account security. Lemma comes back here on its own when you are done."
        >
            {code && (
                <p className="auth__note">
                    Your browser will ask you to confirm this code: <strong className="auth__code">{code}</strong>.
                    Confirm only if it matches.
                </p>
            )}
            <p className="auth__note" role="status">
                {pending ? "Waiting for your browser…" : <LoadingIndicator inline label="Starting sign-in" />}
            </p>
            <div className="screen__actions">
                <button className="btn" disabled={!pending} onClick={() => pending && openBrowser(pending, true)}>
                    Open the browser again
                </button>
            </div>
        </Screen>
    );
}

/** The account this browser is signed in as, for the confirmation to name. */
async function signedInEmail(fetcher: typeof fetch = fetch): Promise<string | null> {
    try {
        const response = await fetcher(onApi("/users/me"), { credentials: "include", cache: "no-store" });
        if (!response.ok) return null;
        const body = (await response.json()) as { email?: unknown };
        return typeof body.email === "string" ? body.email : null;
    } catch {
        return null;
    }
}

/** The browser's side: signed in, ask, tell the backend which app asked, then
 *  wake the app. Reached at `/auth/desktop` from `landing()` while a request
 *  is held.
 *
 *  Never on its own. A request id arrives in a URL, and a URL can be sent by
 *  anybody: completing whatever request a signed-in browser was pointed at
 *  handed the sender's app this person's session. So the request's code is
 *  shown here, beside the account it will sign in, and nothing is completed
 *  until the person — having checked it against the app in front of them —
 *  says so. */
export function DesktopReturn() {
    const [state, setState] = useState<"reading" | "asking" | "handing" | "done" | "declined" | "failed">("reading");
    const [code, setCode] = useState<string | null>(null);
    const [email, setEmail] = useState<string | null>(null);
    const request = useRef<string | null>(null);

    useEffect(() => {
        const requestId = heldRequestId();
        if (!requestId) {
            window.location.replace(PORTAL_PATH);
            return;
        }
        request.current = requestId;
        let cancelled = false;
        void (async () => {
            try {
                if (!(await Session.doesSessionExist())) {
                    /* Not signed in yet — someone opened this address by hand.
                       The request stays held, so signing in comes back here. */
                    window.location.replace(PORTAL_PATH);
                    return;
                }
                const [shown, who] = await Promise.all([handoffCode(requestId), signedInEmail()]);
                if (cancelled) return;
                setCode(shown);
                setEmail(who);
                setState("asking");
            } catch {
                if (!cancelled) setState("failed");
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const confirm = async () => {
        const requestId = request.current;
        if (!requestId) return;
        setState("handing");
        try {
            await completeRequest(requestId);
            dropRequestId();
            setState("done");
            /* A beat, so "you're signed in" is on screen before the browser
               asks whether to open Lemma. */
            window.setTimeout(() => window.location.assign(appReturnUrl(requestId)), 350);
        } catch {
            setState("failed");
        }
    };

    const decline = () => {
        dropRequestId();
        setState("declined");
    };

    if (state === "failed") {
        return (
            <Screen
                title="We couldn’t reach Lemma Desktop"
                lead="Go back to the Lemma app and start signing in again."
            />
        );
    }
    if (state === "declined") {
        return (
            <Screen
                title="Nothing was signed in"
                lead="Lemma Desktop was not given your account. If you did not start this, you can close this tab."
            />
        );
    }
    if (state === "asking") {
        return (
            <Screen
                title={email ? "Sign in to Lemma Desktop as " + email + "?" : "Sign in to Lemma Desktop with this account?"}
                lead="Only continue if you started signing in from the Lemma app on this computer, and it shows this code."
            >
                <p className="auth__note">
                    Code: <strong className="auth__code">{code}</strong>
                </p>
                <div className="screen__actions">
                    <button className="btn btn--primary" onClick={() => void confirm()}>
                        The codes match · sign in
                    </button>
                    <button className="btn" onClick={decline}>
                        Cancel
                    </button>
                </div>
            </Screen>
        );
    }
    return (
        <Screen
            title={state === "done" ? "You’re signed in" : "Signing in to Lemma Desktop…"}
            lead="Lemma Desktop comes to the front and opens your workspace. You can close this tab."
        >
            {state !== "done" && <LoadingIndicator label={state === "reading" ? "Checking this sign-in" : "Handing your session to Lemma Desktop"} />}
        </Screen>
    );
}
