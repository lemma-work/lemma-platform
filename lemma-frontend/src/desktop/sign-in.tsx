"use client";

import { useEffect, useRef, useState } from "react";
import { Screen } from "@/auth/screens";
import { PORTAL_PATH, siteOrigin } from "@/auth/config";
import { landing } from "@/auth/redirects";
import { Session } from "@/auth/supertokens";
import { LoadingIndicator } from "@/ui/loading";
import {
    appReturnUrl,
    awaitSession,
    completeRequest,
    dropPending,
    dropRequestId,
    heldRequestId,
    startHandoff,
    type PendingHandoff,
} from "./auth-handoff";

/** The app's side of a hosted sign-in: open the request, hand the person to
 *  their browser, wait. `auth-handoff.ts` has the whole exchange. */
export function DesktopSignIn({ mode }: { mode: "in" | "up" }) {
    const [pending, setPending] = useState<PendingHandoff | null>(null);
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

/** The browser's side: signed in, tell the backend which app asked, then wake
 *  the app. Reached at `/auth/desktop` from `landing()` while a request is held. */
export function DesktopReturn() {
    const [state, setState] = useState<"handing" | "done" | "failed">("handing");

    useEffect(() => {
        const requestId = heldRequestId();
        if (!requestId) {
            window.location.replace(PORTAL_PATH);
            return;
        }
        let cancelled = false;
        void (async () => {
            try {
                if (!(await Session.doesSessionExist())) {
                    /* Not signed in yet — someone opened this address by hand.
                       The request stays held, so signing in comes back here. */
                    window.location.replace(PORTAL_PATH);
                    return;
                }
                await completeRequest(requestId);
                dropRequestId();
                if (cancelled) return;
                setState("done");
                /* A beat, so "you're signed in" is on screen before the browser
                   asks whether to open Lemma. */
                window.setTimeout(() => window.location.assign(appReturnUrl(requestId)), 350);
            } catch {
                if (!cancelled) setState("failed");
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    if (state === "failed") {
        return (
            <Screen
                title="We couldn’t reach Lemma Desktop"
                lead="Go back to the Lemma app and start signing in again."
            />
        );
    }
    return (
        <Screen
            title={state === "done" ? "You’re signed in" : "Signing in to Lemma Desktop…"}
            lead="Lemma Desktop comes to the front and opens your workspace. You can close this tab."
        >
            {state === "handing" && <LoadingIndicator label="Handing your session to Lemma Desktop" />}
        </Screen>
    );
}
