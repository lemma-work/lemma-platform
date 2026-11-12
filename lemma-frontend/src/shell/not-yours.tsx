import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { MATE } from "@/copy";
import { isForbidden, isMissing } from "@/session/auth-state";
import { lemma } from "@/session/client";
import { useSession } from "@/session/session";
import { LockIcon } from "@/ui/icons";

/** How often a waiting page asks whether the answer has come. */
const WAITING_POLL_MS = 15_000;

/** The door, from outside it.
 *
 *  An address can name a Lemma you are not in. Before this, the shell fell
 *  through to the first pod in your list and showed you that one instead — so
 *  a link somebody sent you opened somebody else's Lemma, with nothing on
 *  screen saying it had happened.
 *
 *  **There is no name to show.** Everything this app knows about a Lemma is
 *  read from the pod, and the pod is what is closed — so every line here is
 *  written to work without one.
 *
 *  **The account is shown instead.** The commonest reason for this screen is
 *  not a closed door but the wrong account: a work link opened in a browser
 *  signed in to a personal one. So the address you are signed in with is on
 *  screen, with a way to switch that comes back to this same link.
 *
 *  **Waiting is watched, not narrated.** The answer is emailed, but a page
 *  left open on "request sent" used to stay on that sentence after it came.
 *  While a request is pending the page asks again every fifteen seconds. A
 *  refusal comes back as one, and says so with a way to ask again; an
 *  approval comes back as no request at all, and the pod list is refetched so
 *  the shell walks in on its own. A pending ask can be taken back.
 */
export function NotYours({
    podId,
    onArrived,
    aside,
}: {
    podId: string;
    /** Membership was made. The shell refetches and walks in. */
    onArrived: () => void;
    /** Another way on, under the account line, for somebody with nowhere
     *  else to go yet. */
    aside?: ReactNode;
}) {
    const queryClient = useQueryClient();
    const session = useSession();

    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        staleTime: 5 * 60_000,
    });

    const ask = useMutation({
        mutationFn: () => source.askToJoin(podId),
        onSuccess: (request) => {
            queryClient.setQueryData(["my-join", podId], request);
            /* An open door admits on the spot: the pod list is what decides
               whether the shell can show this Lemma, so refetching it *is*
               walking in. */
            if (request.standing === "approved") arrive();
        },
    });

    /* Whether this person has already knocked, asked before the button is
       offered, so coming back to a link you have already used says where you
       stand instead of inviting you to ask again. Polled only while pending. */
    const standing = useQuery({
        queryKey: ["my-join", podId],
        queryFn: () => source.myJoinRequest(podId),
        retry: false,
        refetchInterval: (query) => (query.state.data?.standing === "pending" ? WAITING_POLL_MS : false),
    });

    const arrive = useCallback(() => {
        void queryClient.invalidateQueries({ queryKey: ["pods"] });
        onArrived();
    }, [queryClient, onArrived]);

    /* Whether the Lemma opens now. A refusal means the request went away
       without letting you in (withdrawn elsewhere), and the ask stays. Any
       other failure is a check that did not happen, so it is said, with a way
       to run it again — polling has stopped by then, and nothing else would. */
    const [checkFailed, setCheckFailed] = useState(false);
    const checkIn = useCallback(() => {
        setCheckFailed(false);
        void source.getPod(podId).then(
            (pod) => { if (pod) arrive(); },
            (error) => { if (!isForbidden(error) && !isMissing(error)) setCheckFailed(true); },
        );
    }, [podId, arrive]);

    /* Taking it back also makes the request vanish, which must not read as
       being let in. Cleared by the next ask. */
    const tookBack = useRef(false);
    const withdraw = useMutation({
        mutationFn: () => source.withdrawJoinRequest(podId),
        onSuccess: () => {
            tookBack.current = true;
            ask.reset();
            queryClient.setQueryData(["my-join", podId], null);
        },
    });

    /* The platform reports a pending request or a refused one, so an approved
       one reads back as none. Pending, then nothing, is usually being let in
       — but also a withdrawal from another tab, so the Lemma is opened once to
       check before walking in. A refusal there leaves the ask on screen. */
    const wasWaiting = useRef(false);
    const pending = standing.data?.standing === "pending";
    const gone = standing.isSuccess && standing.data == null;
    useEffect(() => {
        if (pending) tookBack.current = false;
        else if (wasWaiting.current && gone && !tookBack.current) {
            checkIn();
        }
        wasWaiting.current = pending;
    }, [pending, gone, checkIn]);

    /* Read from the one query the ask also writes to, so an answer that
       arrives by polling is not outvoted by the click that asked. */
    const waiting = pending;
    const declined = standing.data?.standing === "refused";
    const admitted = ask.data?.standing === "approved";
    const nowhere = isMissing(ask.error);
    const email = me.data?.email ?? null;

    /* Signs out and comes back to this same address, where sign-in returns
       you after. A full load, so nothing the other account could see stays
       in memory. */
    const switchAccount = () => void session.leave(window.location.pathname + window.location.search);

    let title: string;
    let body: ReactNode;
    let action: ReactNode = null;
    if (nowhere) {
        /* Not a locked door — no door. Offering to ask for something that does
           not exist sends somebody to wait for an answer that can never come. */
        title = "This " + MATE + " doesn’t exist";
        body = <p>It may have been deleted, or the link is wrong. Check it with whoever sent it.</p>;
    } else if (admitted) {
        title = "You’re in";
        body = <p>Opening it now.</p>;
        action = (
            <button className="ox-link" onClick={arrive}>Still here? Open it</button>
        );
    } else if (waiting) {
        title = "Request sent";
        body = (
            <p>
                The people who run this {MATE} have it, in Lemma and by email. You’ll get an email
                with the answer, and this page opens on its own if you’re let in.
            </p>
        );
        action = (
            <button className="ox-link" disabled={withdraw.isPending} onClick={() => withdraw.mutate()}>
                {withdraw.isPending ? "Withdrawing…" : "Withdraw request"}
            </button>
        );
    } else if (declined) {
        title = "Your request was declined";
        body = <p>The people who run this {MATE} said no for now. If you still need it, ask one of them, or ask again.</p>;
        action = (
            <button className="ox-btn" disabled={ask.isPending} onClick={() => ask.mutate()}>
                {ask.isPending ? "Asking…" : "Ask again"}
            </button>
        );
    } else {
        title = "You’re not in this " + MATE + " yet";
        body = <p>Ask to join and the people who run it get your request. Some {MATE}s let you straight in.</p>;
        action = (
            <button
                className="ox-btn ox-btn--primary"
                disabled={ask.isPending || standing.isPending}
                onClick={() => ask.mutate()}
            >
                {ask.isPending ? "Asking…" : "Ask to join"}
            </button>
        );
    }

    return (
        <div className="ox-page ox-door">
            <span className="ox-door__mark" aria-hidden="true"><LockIcon size={18} /></span>
            <h1 className="ox-door__t">{title}</h1>
            <div className="ox-door__body">{body}</div>
            {action && <div className="ox-door__actions">{action}</div>}
            {ask.error && !nowhere && (
                <p className="ox-door__error" role="alert">
                    {ask.error instanceof Error ? ask.error.message : "Couldn’t send the request. Try again."}
                </p>
            )}
            {checkFailed && (
                <p className="ox-door__error" role="alert">
                    Couldn’t check whether you’re in yet.{" "}
                    <button className="ox-link" onClick={checkIn}>Check again</button>
                </p>
            )}
            {withdraw.error && (
                <p className="ox-door__error" role="alert">
                    {withdraw.error instanceof Error ? withdraw.error.message : "Couldn’t withdraw the request. Try again."}
                </p>
            )}
            {!nowhere && !admitted && (
                <p className="ox-door__who ox-vs">
                    {email ? <>Signed in as {email}. </> : null}
                    <button className="ox-link" onClick={switchAccount}>Use another account</button>
                </p>
            )}
            {aside && <p className="ox-door__aside ox-vs">{aside}</p>}
            {/* Which link this was, for somebody who has been sent two:
                small, last, and plain text rather than a chip. */}
            <p className="ox-door__which ox-vs">Link {podId}</p>
        </div>
    );
}
