import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, askerName, type JoinRequest } from "@/data";
import { agoOf } from "@/schedule/schedules";
import { isForbidden } from "@/session/auth-state";

/** Who is waiting to be let in, for whoever can let them.
 *
 *  The other half of the Request button. `joinChoices` promises that the ask
 *  "arrives here and by email" — the email has always been sent, and *here*
 *  was nowhere, so a shut door queued requests into a place no screen in this
 *  app could read. A queue nobody can see is worse than no queue: the asker is
 *  told to wait for an answer that is never coming.
 *
 *  It sits inside the roster, above the door control, because the three are
 *  one question asked in three tenses: who is in, who is asking, who could.
 *
 *  **Listing is an admin's call and most people are not one**, so a refusal is
 *  the ordinary case rather than a fault, and it draws nothing at all. Any
 *  other failure gets a line, because that one is a fault.
 */
export function AtTheDoor({ podId, teammate }: { podId: string; teammate: string }) {
    const queryClient = useQueryClient();

    const waiting = useQuery({
        queryKey: ["pod-knocking", podId],
        queryFn: () => source.listJoinRequests(podId),
        retry: false,
    });

    const admit = useMutation({
        mutationFn: (request: JoinRequest) => source.admitToPod(podId, request.id),
        onSuccess: (_answer, request) => {
            /* Patched, not invalidated: refetching the queue to learn that a
               row somebody just clicked is gone is a round trip to be told
               what the click already said. */
            queryClient.setQueryData<JoinRequest[]>(["pod-knocking", podId], (before) =>
                (before ?? []).filter((one) => one.id !== request.id),
            );
            /* The roster is a different query and has genuinely changed —
               somebody is in it now — so that one really does have to be
               asked again. */
            void queryClient.invalidateQueries({ queryKey: ["pod-detail", podId] });
        },
    });

    if (isForbidden(waiting.error)) return null;
    if (waiting.error) return <p className="empty-row">Couldn’t load access requests.</p>;

    const queue = waiting.data ?? [];
    /* Nothing is drawn for an empty queue. The door control directly below
       already says who may knock, and "nobody is waiting" under it is the app
       narrating its own quiet state. */
    if (queue.length === 0) return null;

    return (
        <div className="knocking">
            <p className="knocking__ask">
                {queue.length === 1 ? "Someone is asking" : queue.length + " people are asking"} to join {teammate}
            </p>
            <ul className="knocking__list">
                {queue.map((request) => (
                    <li key={request.id}>
                        <span className="knocking__body">
                            <span className="knocking__who">{askerName(request)}</span>
                            {/* The address as well as the name, where there is
                                both: letting somebody into a teammate is a
                                decision about a person, and two colleagues can
                                share a first and last name. */}
                            <span className="knocking__mail">
                                {request.name && request.email ? request.email : ""}
                                {request.askedAt ? (request.name && request.email ? " · " : "") + agoOf(request.askedAt) : ""}
                            </span>
                        </span>
                        <button
                            className="btn"
                            disabled={admit.isPending}
                            onClick={() => admit.mutate(request)}
                        >
                            {admit.isPending && admit.variables?.id === request.id ? "Approving…" : "Approve request"}
                        </button>
                    </li>
                ))}
            </ul>
            {admit.error && (
                <p className="knocking__note" role="alert">
                    {admit.error instanceof Error ? admit.error.message : "That could not be done."}
                </p>
            )}
            {/* Said rather than drawn as a disabled button. The platform has a
                REJECTED status and no endpoint that sets it, so there is no
                declining to offer — and a Decline button that always failed
                would be a worse answer than the truth. */}
            <p className="knocking__note">
                Requests stay pending until approved.
            </p>
        </div>
    );
}
