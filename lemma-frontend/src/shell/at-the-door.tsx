import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, askerName, POD_ROLES, type JoinRequest } from "@/data";
import { agoOf } from "@/schedule/schedules";
import { isForbidden } from "@/session/auth-state";

/** The roles an approver can let somebody in as, widest last. */
const ROLES = [...POD_ROLES.slice(1), POD_ROLES[0]];
/** What somebody who asked is let in as unless the approver says otherwise:
 *  using it, the least that answers a request to join. */
const LET_IN_AS = "POD_USER";

/** Who is waiting to be let in, for whoever can let them.
 *
 *  The other half of the Request button. `joinChoices` promises that the ask
 *  "arrives here and by email" — the email has always been sent, and *here*
 *  was nowhere, so a shut door queued requests into a place no screen in this
 *  app could read. A queue nobody can see is worse than no queue: the asker is
 *  told to wait for an answer that is never coming.
 *
 *  Each row is answered both ways, and the asker hears either: approving
 *  lets them in as the role picked on the row, declining tells them no. Both
 *  send them an email, so neither needs a note here saying what happens next.
 *
 *  **Listing is an admin's call and most people are not one**, so a refusal is
 *  the ordinary case rather than a fault, and it draws nothing at all. Any
 *  other failure gets a line, because that one is a fault.
 */
export function AtTheDoor({ podId, teammate }: { podId: string; teammate: string }) {
    const queryClient = useQueryClient();
    const [roles, setRoles] = useState<Record<string, string>>({});

    const waiting = useQuery({
        queryKey: ["pod-knocking", podId],
        queryFn: () => source.listJoinRequests(podId),
        retry: false,
    });

    const answer = useMutation({
        mutationFn: ({ request, admit }: { request: JoinRequest; admit: boolean }) =>
            admit
                ? source.admitToPod(podId, request.id, roles[request.id] ?? LET_IN_AS)
                : source.declineJoinRequest(podId, request.id),
        onSuccess: (_answer, { request, admit }) => {
            /* Patched, not invalidated: refetching the queue to learn that a
               row somebody just clicked is gone is a round trip to be told
               what the click already said. */
            queryClient.setQueryData<JoinRequest[]>(["pod-knocking", podId], (before) =>
                (before ?? []).filter((one) => one.id !== request.id),
            );
            /* An admission changes the roster, which is a different query. */
            if (admit) {
                void queryClient.invalidateQueries({ queryKey: ["pod-detail", podId] });
                void queryClient.invalidateQueries({ queryKey: ["pods"] });
            }
        },
    });

    if (isForbidden(waiting.error)) return null;
    if (waiting.error) return <p className="empty-row">Couldn’t load access requests.</p>;

    const queue = waiting.data ?? [];
    /* Nothing is drawn for an empty queue. The door control already says who
       may knock, and "nobody is waiting" is the app narrating its own quiet. */
    if (queue.length === 0) return null;

    const busyWith = answer.isPending ? answer.variables : null;

    return (
        <div className="knocking">
            <p className="knocking__ask">
                {queue.length === 1 ? "Someone is asking" : queue.length + " people are asking"} to join {teammate}
            </p>
            <ul className="knocking__list">
                {queue.map((request) => {
                    const busy = busyWith?.request.id === request.id;
                    return (
                        <li key={request.id}>
                            <span className="knocking__body">
                                <span className="knocking__who">{askerName(request)}</span>
                                {/* The address as well as the name, where there
                                    is both: letting somebody in is a decision
                                    about a person, and two colleagues can share
                                    a first and last name. */}
                                <span className="knocking__mail">
                                    {request.name && request.email ? request.email : ""}
                                    {request.askedAt ? (request.name && request.email ? " · " : "") + agoOf(request.askedAt) : ""}
                                </span>
                            </span>
                            <span className="knocking__acts">
                                <select
                                    className="addpeople__role"
                                    aria-label={"Let " + askerName(request) + " in as"}
                                    value={roles[request.id] ?? LET_IN_AS}
                                    disabled={answer.isPending}
                                    onChange={(event) => setRoles((was) => ({ ...was, [request.id]: event.target.value }))}
                                >
                                    {ROLES.map((option) => (
                                        <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                </select>
                                <button
                                    className="btn btn--primary"
                                    disabled={answer.isPending}
                                    onClick={() => answer.mutate({ request, admit: true })}
                                >
                                    {busy && busyWith?.admit ? "Approving…" : "Approve"}
                                </button>
                                <button
                                    className="btn"
                                    disabled={answer.isPending}
                                    onClick={() => answer.mutate({ request, admit: false })}
                                >
                                    {busy && !busyWith?.admit ? "Declining…" : "Decline"}
                                </button>
                            </span>
                        </li>
                    );
                })}
            </ul>
            {answer.error && (
                <p className="knocking__note" role="alert">
                    {answer.error instanceof Error ? answer.error.message : "That could not be done."}
                </p>
            )}
        </div>
    );
}
