"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Invitation } from "@/data";
import { outcomeView } from "./address";
import type { Go } from "./model";

/** Invitations waiting on somebody who already belongs somewhere.
 *
 *  Only the arrival screens used to read them, and those are shown only to
 *  somebody with no organization at all — so once you had one, an invitation
 *  to another Lemma could be found only by digging out its email. They sit at
 *  the top of Home now, each with its two answers. Nothing is drawn when none
 *  are waiting, or when they could not be read: an empty box on Home is noise,
 *  and the email is still there.
 */
export function Invited({ go }: { go: Go }) {
    const queryClient = useQueryClient();
    const invitations = useQuery({ queryKey: ["my-invitations"], queryFn: () => source.myInvitations(), staleTime: 60_000 });

    const answer = useMutation({
        mutationFn: async ({ invite, accept }: { invite: Invitation; accept: boolean }) => {
            if (accept) await source.acceptInvitation(invite.id);
            else await source.declineInvitation(invite.id);
        },
        onSuccess: async (_none, { invite, accept }) => {
            queryClient.setQueryData<Invitation[]>(["my-invitations"], (was) => (was ?? []).filter((one) => one.id !== invite.id));
            if (!accept) return;
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ["orgs"] }),
                queryClient.invalidateQueries({ queryKey: ["pods"] }),
            ]);
            if (invite.podId) go(outcomeView(invite.podId));
        },
    });

    const waiting = invitations.data ?? [];
    if (waiting.length === 0) return null;
    const busyWith = answer.isPending ? answer.variables : null;

    return (
        <section aria-label="Invitations">
            <div className="ox-sec"><h2>Invited</h2></div>
            <div className="ox-invited">
                {waiting.map((invite) => {
                    const busy = busyWith?.invite.id === invite.id;
                    return (
                        <div key={invite.id} className="ox-invited__row">
                            <span className="ox-invited__t">
                                {invite.podName
                                    ? <>{invite.podName} <span className="ox-vs">at {invite.orgName}</span></>
                                    : <>{invite.orgName} <span className="ox-vs">organization</span></>}
                            </span>
                            <span className="ox-invited__acts">
                                <button type="button" className="ox-btn ox-btn--small ox-btn--primary" disabled={answer.isPending} onClick={() => answer.mutate({ invite, accept: true })}>
                                    {busy && busyWith?.accept ? "Joining…" : "Accept"}
                                </button>
                                <button type="button" className="ox-btn ox-btn--small" disabled={answer.isPending} onClick={() => answer.mutate({ invite, accept: false })}>
                                    {busy && !busyWith?.accept ? "Declining…" : "Decline"}
                                </button>
                            </span>
                        </div>
                    );
                })}
            </div>
            {answer.error && (
                <p className="ox-note" role="alert">
                    {answer.error instanceof Error ? answer.error.message : "That couldn’t be done. Try again."}
                </p>
            )}
        </section>
    );
}
