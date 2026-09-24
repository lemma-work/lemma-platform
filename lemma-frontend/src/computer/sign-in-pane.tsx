"use client";

import { LoadingIndicator } from "@/ui/loading";

import { useState } from "react";
import { ExternalIcon, LockIcon, WarningIcon } from "@/ui/icons";
import { live } from "@/usage/queries";
import { LiveScreen } from "./live-screen";
import { outcomeSay, whereabouts } from "./sign-in";
import { useAnswerSignIn, useCurrentPage, useOpenBrowserTab, usePendingSignIn } from "./queries";
import { type LiveState } from "./live";

/** One paused `browser_sign_in`, answerable.
 *
 *  A teammate has stopped mid-run because a site wants a person. This is where
 *  that person goes: the teammate's own browser, drivable, pointed at the site,
 *  with the two buttons that let the run carry on.
 *
 *  **The answer is the part that must not be missing.** Nothing detects a
 *  completed login on its own — there is no polling, no cookie watch, no
 *  heuristic that closes this on your behalf. A surface that showed the browser
 *  without these controls would let somebody sign in and leave the teammate
 *  waiting for ever, which is precisely what this app did before: the card in
 *  the transcript linked out to another deployment's page and hoped it was
 *  there.
 *
 *  Control rather than watching, and no toggle to arm first. There is no lease
 *  to take: the sandbox has one browser, two parties acting at once costs a
 *  retry, and a retry is cheaper than a person staring at a password field that
 *  will not take their keystrokes.
 */
export function SignInPane({ conversationId, toolCallId, onDone }: {
    conversationId: string;
    toolCallId: string;
    /** Told when the pause is answered, so a host holding this open can put
     *  itself away rather than leaving a spent sign-in on screen. */
    onDone?: () => void;
}) {
    /* Sample mode has no account behind it, so there is no paused run to ask
       about and no browser to show. Said here rather than left to the queries,
       which are disabled in that mode and would leave this waiting on an answer
       that is never coming. */
    const sample = !live();
    const request = usePendingSignIn(conversationId, toolCallId);
    const answer = useAnswerSignIn(conversationId, toolCallId);
    const [picture, setPicture] = useState<LiveState>("connecting");
    /* Bumped to open the socket again, which is what re-steers: the server
       points the browser at the site on every connect. The case it is for is
       the ordinary one — somebody answers hours later, from a notification,
       and the browser the steer was aimed at has been retired since. */
    const [reopen, setReopen] = useState(0);
    const tab = useOpenBrowserTab();

    const origin = request.data?.origin ?? null;
    /* Only while the picture is up and the pause is open. It is a poll, and a
       poll against a sandbox behind a pane nobody is looking at is a round trip
       spent on nothing. */
    const page = useCurrentPage(origin, picture === "live" && !answer.data);

    if (sample) {
        return (
            <div className="computer-empty">
                <LockIcon size={26} />
                <p>
                    In the sample there is no machine and no waiting teammate. Signed in, this is your
                    teammate&rsquo;s own browser, open on the site, with your keyboard in it.
                </p>
            </div>
        );
    }

    if (request.isPending) return <p className="computer-note" role="status">Asking what this is for…</p>;

    if (request.isError || !request.data) {
        return (
            <div className="computer-empty">
                <WarningIcon size={26} />
                <p>
                    This sign-in is not for the account you are signed in to here. Ask the teammate to send
                    it again, to this one.
                </p>
            </div>
        );
    }

    if (answer.data) {
        const said = outcomeSay(answer.data.signed_in, answer.data.working);
        return (
            <div className="computer-empty">
                {answer.data.signed_in ? <LockIcon size={26} /> : <WarningIcon size={26} />}
                <p><strong>{said.headline}.</strong> {said.note}</p>
            </div>
        );
    }

    const where = whereabouts(request.data.origin, page.data ?? null);

    return (
        <div className="signin">
            <header className="signin__head">
                <p className="signin__host">
                    <LockIcon size={15} className={where.secure ? "signin__lock" : "signin__lock--open"} />
                    {/* The host the browser is actually on, as the sandbox
                        reports it. This is what somebody checks before they
                        type a password, so it tracks the page rather than the
                        request that started it. */}
                    <strong>{where.host}</strong>
                    {!where.secure && <span className="signin__warn">not a secure connection</span>}
                    {where.elsewhere && <span className="signin__aside">signing in to {whereabouts(request.data.origin, null).host}</span>}
                </p>
                {request.data.reason && (
                    /* The teammate's own words, quoted as theirs rather than
                       presented as this app speaking. */
                    <p className="signin__reason">The teammate says: &ldquo;{request.data.reason}&rdquo;</p>
                )}
                <p className="signin__fine">
                    Sign in below as you normally would. The browser keeps the session so your teammate can
                    carry on, and your password never reaches Lemma.
                </p>
            </header>

            <div className="signin__glass">
                <LiveScreen
                    mode="control"
                    origin={request.data.origin}
                    reopen={reopen}
                    onState={setPicture}
                />
                {/* Steering is not instant and until it is visible it reads as
                    a broken pane: opening a sign-in points the browser at the
                    site and that browser may be cold, so the picture paints a
                    blank page first. Answering late is the normal case for a
                    question that pauses a run, so this has to read as progress
                    rather than as an empty browser. */}
                {picture === "live" && !where.arrived && (
                    <div className="signin__waiting">
                        <LoadingIndicator label={"Connecting to " + whereabouts(request.data.origin, null).host} />
                        {/* The whole of what somebody can do about a steer
                            that has not landed, and it is worth a control:
                            reconnecting is what sends the browser at the site
                            again. Without it this sentence was a wait with no
                            end and no handle. */}
                        <button className="signin__again" onClick={() => setReopen((n) => n + 1)}>
                            Try again
                        </button>
                    </div>
                )}
                {picture !== "live" && (
                    <p className="signin__waiting" role="status">
                        {picture === "connecting" ? "Connecting to the browser…" : liveTrouble(picture)}
                    </p>
                )}
            </div>

            {(picture === "refused" || picture === "unsupported" || picture === "stale-image") && (
                <p className="computer-note">
                    <button className="computer-inline" disabled={tab.busy} onClick={tab.open}>
                        <ExternalIcon size={13} /> {tab.busy ? <LoadingIndicator inline label="Loading" /> : "Open the browser in a tab instead"}
                    </button>
                </p>
            )}

            {answer.isError && (
                <p className="computer-note" role="alert">
                    That did not reach the teammate. It may have stopped waiting — try again, and if it keeps
                    failing you can close this and tell it in the conversation.
                </p>
            )}

            <footer className="signin__foot">
                {/* Both answers, always. "I could not" is not a cancel: it is
                    what lets a run end rather than sit waiting on somebody who
                    has already given up. */}
                <button
                    className="btn"
                    disabled={answer.isPending}
                    onClick={() => answer.mutate(false, { onSuccess: onDone })}
                >
                    Can&rsquo;t right now
                </button>
                <button
                    className="btn btn--primary"
                    disabled={answer.isPending}
                    onClick={() => answer.mutate(true, { onSuccess: onDone })}
                >
                    {answer.isPending ? "Checking…" : "I'm signed in"}
                </button>
            </footer>
        </div>
    );
}

/** Why the picture is not there, in a sentence somebody can act on. */
function liveTrouble(state: LiveState): string {
    if (state === "no-browser") return "The browser has not started yet. This will pick up when it does.";
    if (state === "signed-out") return "Your session ended. Sign in to Lemma again to carry on.";
    if (state === "unsupported") return "This kind of machine has no screen to show.";
    if (state === "stale-image") return "This machine is running an image too old to show its screen.";
    if (state === "refused") return "This app is not on the API's allowlist for the screen, so it cannot carry the picture.";
    return "The connection dropped. Trying again…";
}
