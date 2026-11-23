import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { MATE } from "@/copy";
import { isForbidden, isMissing } from "@/session/auth-state";
import { lemma } from "@/session/client";
import { useSession } from "@/session/session";
import { Calm, Problem } from "@/outcomes/calm";
import { Icon } from "@/outcomes/parts";

/** How often a waiting page asks whether the answer has come. */
const WAITING_POLL_MS = 15_000;

/** The door, from outside it.
 *
 *  An address can name a Lemma you are not in. Before this, the shell fell
 *  through to the first pod in your list and showed you that one instead — so
 *  a link somebody sent you opened somebody else's Lemma, with nothing on
 *  screen saying it had happened.
 *
 *  **There is usually no name to show.** Everything this app knows about a
 *  Lemma is read from the pod, and the pod is what is closed — so every line
 *  here is written to work without one, and uses the name and the face
 *  (dimmed) only where the caller already has them.
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
    name,
    face,
    onHome,
}: {
    podId: string;
    /** Membership was made. The shell refetches and walks in. */
    onArrived: () => void;
    /** Another way on, under the account line, for somebody with nowhere
     *  else to go yet. */
    aside?: ReactNode;
    /** The Lemma's name and face, where they are known without reading it. */
    name?: string;
    face?: ReactNode;
    /** Where to go when the Lemma turns out not to exist. */
    onHome?: () => void;
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
    /* An answer that lands after this page has gone — the person moved on —
       must not walk them anywhere. */
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    const checkIn = useCallback(() => {
        setCheckFailed(false);
        void source.getPod(podId).then(
            (pod) => { if (pod && mounted.current) arrive(); },
            (error) => { if (mounted.current && !isForbidden(error) && !isMissing(error)) setCheckFailed(true); },
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
    /* The closed Lemma's own name and face, where whoever drew this knows
       them; the door is written to read right without either. */
    const called = name ?? "this " + MATE;

    /* Signs out and comes back to this same address, where sign-in returns
       you after. A full load, so nothing the other account could see stays
       in memory. */
    const switchAccount = () => void session.leave(window.location.pathname + window.location.search);
    const otherAccount = <button type="button" className="b b--m b--secondary" onClick={switchAccount}>Use another account</button>;
    const signedInAs = email ? <>Signed in as {email}</> : null;

    let calm: ReactNode;
    if (nowhere) {
        /* Not a locked door — no door. Offering to ask for something that does
           not exist sends somebody to wait for an answer that can never come. */
        calm = (
            <Calm
                icon="search"
                title={"This " + MATE + " isn’t here"}
                actions={onHome ? <button type="button" className="b b--m b--primary" onClick={onHome}>Home</button> : undefined}
            >
                <p>It was deleted, or the link came from somewhere else.</p>
            </Calm>
        );
    } else if (admitted) {
        calm = (
            <Calm
                art={face ? <span className="kcalm__face">{face}</span> : undefined}
                icon="check"
                title="You’re in"
                actions={<button type="button" className="b b--m b--primary" onClick={arrive}>Still here? Open it</button>}
            >
                <p>Opening it now.</p>
            </Calm>
        );
    } else if (waiting) {
        calm = (
            <Calm
                art={face ? <span className="kcalm__face">{face}</span> : undefined}
                icon="lock"
                title={"Asked to join " + called}
                actions={(
                    <button type="button" className="b b--m b--secondary" disabled={withdraw.isPending} onClick={() => withdraw.mutate()}>
                        {withdraw.isPending ? "Withdrawing…" : "Withdraw request"}
                    </button>
                )}
                note={signedInAs}
            >
                <p>You’re in as soon as one of its admins says yes.</p>
                {checkFailed && <Problem onRetry={checkIn}>Couldn’t check whether you’re in yet.</Problem>}
                {withdraw.isError && <Problem onRetry={() => withdraw.mutate()}>Couldn’t withdraw the request.</Problem>}
            </Calm>
        );
    } else if (declined) {
        calm = (
            <Calm
                art={face ? <span className="kcalm__face kcalm__face--off">{face}</span> : undefined}
                icon="lock"
                title="Your request was declined"
                actions={(
                    <>
                        {otherAccount}
                        <button type="button" className="b b--m b--primary" disabled={ask.isPending} onClick={() => ask.mutate()}>
                            {ask.isPending ? "Asking…" : "Ask again"}
                        </button>
                    </>
                )}
                note={signedInAs}
            >
                <p>Its admins said no for now. If you still need it, ask one of them, or ask again.</p>
                {ask.isError && <Problem onRetry={() => ask.mutate()}>Couldn’t ask to join.</Problem>}
            </Calm>
        );
    } else {
        calm = (
            <Calm
                art={face ? <span className="kcalm__face kcalm__face--off">{face}</span> : undefined}
                icon="lock"
                title={"You’re not in " + called}
                actions={(
                    <>
                        {otherAccount}
                        <button
                            type="button"
                            className="b b--m b--primary"
                            disabled={ask.isPending || standing.isPending}
                            onClick={() => ask.mutate()}
                        >
                            <Icon name="lock" size={16} />
                            {ask.isPending ? "Asking…" : "Ask to join"}
                        </button>
                    </>
                )}
                note={signedInAs}
            >
                <p>{name ? name + " is" : "It’s"} only for the people added to it. Ask to join, and its admins see it in People.</p>
                {ask.isError && <Problem onRetry={() => ask.mutate()}>Couldn’t ask to join.</Problem>}
                {checkFailed && <Problem onRetry={checkIn}>Couldn’t check whether you’re in yet.</Problem>}
            </Calm>
        );
    }

    return (
        <div className="kdoor">
            {calm}
            {aside && <p className="kdoor__aside">{aside}</p>}
        </div>
    );
}
