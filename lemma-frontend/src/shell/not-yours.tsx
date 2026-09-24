import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { MATE } from "@/copy";
import { isMissing } from "@/session/auth-state";

/** The door, from outside it.
 *
 *  An address can name a teammate you are not in. Before this, the shell fell
 *  through to the first pod in your list and showed you that one instead — so
 *  a link somebody sent you opened somebody else's teammate, with nothing on
 *  screen saying it had happened. The address bar then either lied about where
 *  you were or quietly rewrote itself, and both are worse than a wall.
 *
 *  `joinChoices` has been telling whoever runs a pod that "anyone else gets a
 *  Request button; the ask arrives here and by email" since the door control
 *  was built. This is the Request button. It was the only part of that
 *  sentence with nothing behind it.
 *
 *  **There is no name to show.** Everything this app knows about a teammate is
 *  read from the pod, and the pod is what is closed — so this screen has an id
 *  and nothing else, and every line on it is written to work without one. The
 *  id is shown anyway, small: somebody who has been sent two links needs to
 *  know which one they are looking at.
 */
export function NotYours({
    podId,
    onArrived,
}: {
    podId: string;
    /** Membership was made. The shell refetches and walks in. */
    onArrived: () => void;
}) {
    const queryClient = useQueryClient();

    /* Whether this person has already knocked. Asked before the button is
       offered, so coming back to a link you have already used says where you
       stand instead of inviting you to ask again — which reads as though the
       first ask went nowhere. */
    const standing = useQuery({
        queryKey: ["my-join", podId],
        queryFn: () => source.myJoinRequest(podId),
        retry: false,
    });

    const ask = useMutation({
        mutationFn: () => source.askToJoin(podId),
        onSuccess: (request) => {
            queryClient.setQueryData(["my-join", podId], request);
            /* An open door admits on the spot, and then there is nothing to
               wait for: the pod list is what decides whether the shell can
               show this teammate, so refetching it *is* walking in. Telling
               somebody to reload a page that could refresh itself is the app
               asking the person to do its work. */
            if (request.standing === "approved") {
                void queryClient.invalidateQueries({ queryKey: ["pods"] });
                onArrived();
            }
        },
    });

    const waiting = standing.data?.standing === "pending" || ask.data?.standing === "pending";
    /* Admitted, and still looking at the door — which is a real moment, not a
       impossible one: the membership is made the instant an open door is
       knocked on, and this screen stays up for as long as the pod list takes
       to come back with the teammate in it. Without a state of its own that
       window falls through to the ask, so somebody who has just been let in is
       invited to ask again. */
    const admitted = standing.data?.standing === "approved" || ask.data?.standing === "approved";
    const nowhere = isMissing(ask.error);

    return (
        <div className="screen">
            <div className="screen__inner">
                {nowhere ? (
                    <>
                        {/* Not a locked door — no door. Said as its own thing,
                            because offering to ask for access to something that
                            does not exist sends somebody to wait for an answer
                            that can never come. */}
                        <h2>No such {MATE}</h2>
                        <p>
                            This link points at a teammate that does not exist, or one that has since
                            been deleted. Check the link with whoever sent it.
                        </p>
                    </>
                ) : admitted ? (
                    <>
                        <h2>You are in</h2>
                        <p>The door was already open to you. Fetching this {MATE} now.</p>
                        {/* A way out of a wait that has stopped moving. The
                            list is what decides whether the stage can take
                            over, so asking for it again is the only useful
                            thing left on this screen. */}
                        <div className="screen__actions">
                            <button
                                className="screen__aside"
                                onClick={() => { void queryClient.invalidateQueries({ queryKey: ["pods"] }); onArrived(); }}
                            >
                                Still here? Look again
                            </button>
                        </div>
                    </>
                ) : waiting ? (
                    <>
                        <h2>Access request sent</h2>
                        <p>
                            Whoever runs this {MATE} has it, here and by email. You will be added
                            once one of them says yes — nothing else is needed from you.
                        </p>
                    </>
                ) : (
                    <>
                        <h2>This {MATE} is not one of yours</h2>
                        <p>
                            Your account does not currently have access to this teammate. You can ask to join.
                        </p>
                        {/* Deliberately not a promise about which. The pod's own
                            policy decides whether this admits on the spot or
                            waits for somebody, and it is not readable from out
                            here — so the button asks, and the answer says. */}
                        <div className="screen__actions">
                            <button
                                className="btn btn--primary"
                                disabled={ask.isPending || standing.isPending}
                                onClick={() => ask.mutate()}
                            >
                                {ask.isPending ? "Asking…" : "Ask to join"}
                            </button>
                        </div>
                    </>
                )}

                {ask.error && !nowhere && (
                    <p role="alert">{ask.error instanceof Error ? ask.error.message : "Couldn’t send the access request. Try again."}</p>
                )}

                {/* Which link this was. Small, and last: this screen is one
                    somebody screenshots to ask a colleague for access, and the
                    address bar does not survive a screenshot. Its own quiet
                    style rather than `screen__footnote`, whose divider would
                    read as separating the id from the button above it — a
                    rule between two things that belong together. */}
                <p className="screen__which"><code>{podId}</code></p>
            </div>
        </div>
    );
}
