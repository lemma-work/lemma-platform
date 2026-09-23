"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { source } from "@/data";
import type { Pod } from "@/data";
import { CloseIcon, WorkflowIcon } from "@/ui/icons";
import { WaitForm } from "./form";
import {
    byStuckLongest, readAssignments, readWorkflows, sayStuckFor, sayWaitingOn,
    type Assignment,
} from "./runs";

/** What is stuck on you.
 *
 *  `workflows.runs.waitingAssignedToMe` exists for one question and this app
 *  asked it nowhere: a workflow that stops on a form assigned to a person can
 *  sit for days, and until now the only sign of it was a notification, which
 *  is a message that can be read and forgotten. A wait is not a message. It is
 *  a piece of work with this person's name on it, and it stays owed until it
 *  is answered.
 *
 *  Beside the bell rather than inside it on purpose. The notification panel is
 *  a log of things said to you, most of which are over; this is a queue of
 *  things not done. Folding the queue into the log is how the queue stops
 *  being looked at.
 */
export function WaitingInbox({ pods }: {
    /** The pods this person belongs to in the organization on screen, already
     *  loaded for the rail. See the fan-out note below for why a list is
     *  wanted rather than one id. */
    pods: Pod[];
}) {
    const [open, setOpen] = useState(false);
    const [showing, setShowing] = useState<string | null>(null);
    /* Measured from the button and drawn in a portal, for the same reason the
       notification panel is: `.head` sets `overflow: hidden` so its collapse
       animates to a real height, and an absolutely positioned child of the
       header is clipped at the header's bottom edge. It is not a z-index
       problem — the panel is never painted at all. */
    const [at, setAt] = useState<{ top: number; right: number } | null>(null);
    const anchor = useRef<HTMLButtonElement | null>(null);
    const sheet = useRef<HTMLDivElement | null>(null);
    const cache = useQueryClient();
    const sample = source.label === "sample";

    const podKey = pods.map((pod) => pod.id).join(",");
    const queue = useQuery({
        queryKey: ["workflow-waiting", podKey],
        queryFn: () => gather(pods, sample),
        enabled: pods.length > 0,
        staleTime: 60_000,
        refetchOnWindowFocus: true,
    });

    useEffect(() => {
        if (!open) { setAt(null); return; }
        const place = () => {
            const rect = anchor.current?.getBoundingClientRect();
            if (!rect) return;
            setAt({ top: rect.bottom + 8, right: Math.max(16, window.innerWidth - rect.right) });
        };
        place();
        const away = (event: MouseEvent) => {
            const target = event.target as Node;
            if (anchor.current?.contains(target) || sheet.current?.contains(target)) return;
            setOpen(false);
        };
        const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
        document.addEventListener("mousedown", away);
        document.addEventListener("keydown", escape);
        window.addEventListener("resize", place);
        window.addEventListener("scroll", place, true);
        return () => {
            document.removeEventListener("mousedown", away);
            document.removeEventListener("keydown", escape);
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", place, true);
        };
    }, [open]);

    const rows = queue.data?.rows ?? [];
    const count = rows.length;
    /* Nothing owed: no control. An icon that is always there, permanently
       showing zero, is one more thing in a header that already carries seven,
       and this one has nothing to say.
     *
     *  Pending counts as nothing rather than as something. The query is
       disabled until the pod list lands, so `isPending` is true from the first
       paint — rendering through it put a badge-less icon in the header on
       every load, which then vanished a moment later on the common day when
       nothing is owed. A failed read does render, because "your queue could
       not be read" is the one thing worse than an empty queue to leave
       unsaid.
     *
     *  `!open` guards it, and that is not belt-and-braces: answering the last
       form empties the list, and without the guard the control would unmount
       under the cursor the instant somebody finished — panel and all — rather
       than saying the queue is clear. */
    if (!open && count === 0 && !queue.isError) return null;

    const refresh = () => { void cache.invalidateQueries({ queryKey: ["workflow-waiting"] }); };

    return (
        <div className="wf-inbox">
            <button
                ref={anchor}
                className="icon-button wf-inbox__open"
                title={count ? count + " waiting on you" : "Workflows waiting on you"}
                aria-label={count ? "Workflows waiting on you, " + count : "Workflows waiting on you"}
                aria-expanded={open}
                onClick={() => setOpen((was) => !was)}
            >
                <WorkflowIcon size={19} />
                {count > 0 && <span className="wf-inbox__badge" aria-hidden="true">{count > 99 ? "99+" : count}</span>}
            </button>

            {open && at && createPortal((
                <div
                    className="wf-inbox__panel"
                    role="dialog"
                    aria-label="Workflows waiting on you"
                    ref={sheet}
                    style={{ top: at.top, right: at.right }}
                >
                    <header className="wf-inbox__head">
                        <h2>Waiting on you</h2>
                        <button className="icon-button" aria-label="Close" onClick={() => setOpen(false)}>
                            <CloseIcon size={16} />
                        </button>
                    </header>

                    {queue.isPending && <p className="wf-quiet" role="status">Reading…</p>}
                    {queue.isError && (
                        <p className="wf-quiet" role="alert">
                            Couldn’t load workflows waiting for your input.{" "}
                            <button onClick={() => void queue.refetch()}>Try again</button>
                        </p>
                    )}
                    {/* Partial is not failed. One pod refusing the list is a
                        permission answer about that pod, and emptying the
                        queue over it would hide work that is genuinely owed
                        somewhere else. */}
                    {(queue.data?.unreadable ?? 0) > 0 && (
                        <p className="wf-inbox__partial">
                            {queue.data?.unreadable === 1
                                ? "One teammate’s queue could not be read, so this may be short."
                                : queue.data?.unreadable + " teammates’ queues could not be read, so this may be short."}
                        </p>
                    )}
                    {queue.isSuccess && rows.length === 0 && (
                        <p className="wf-quiet">No workflows need your input.</p>
                    )}

                    <div className="wf-inbox__list">
                        {rows.map((row) => (
                            <WaitingRow
                                key={row.wait.id}
                                row={row}
                                open={showing === row.wait.id}
                                onToggle={() => setShowing((was) => (was === row.wait.id ? null : row.wait.id))}
                                onAnswered={() => { setShowing(null); refresh(); }}
                            />
                        ))}
                    </div>
                </div>
            ), document.body)}
        </div>
    );
}

function WaitingRow({ row, open, onToggle, onAnswered }: {
    row: Row;
    open: boolean;
    onToggle: () => void;
    onAnswered: () => void;
}) {
    const { wait, run, workflowName, podName } = row;

    return (
        <article className="wf-wait" data-open={open ? "" : undefined}>
            <button className="wf-wait__open" aria-expanded={open} onClick={onToggle}>
                <span className="wf-wait__body">
                    <strong>{workflowName}</strong>
                    {/* The pod, because this list crosses them. Which teammate
                        asked is half of what the row is. */}
                    <small>{podName}{wait.nodeId ? " · " + wait.nodeId : ""}</small>
                </span>
                <span className="wf-wait__age">{sayStuckFor(wait, run)}</span>
            </button>

            {open && (
                <div className="wf-wait__form">
                    {wait.type === "HUMAN" ? (
                        <WaitForm podId={row.podId} runId={run.id} wait={wait} onDone={onAnswered} />
                    ) : (
                        /* The endpoint lists active form waits assigned to the
                           caller, so this should not arrive. If it ever does,
                           say what it is rather than drawing a form that would
                           be refused: only a HUMAN wait can be submitted
                           (`execution/engine.py:258`). */
                        <p className="wf-note">{sayWaitingOn(wait.type)} — there is nothing for you to fill in here.</p>
                    )}
                </div>
            )}
        </article>
    );
}

interface Row {
    wait: Assignment["wait"];
    run: Assignment["run"];
    podId: string;
    podName: string;
    workflowName: string;
}

/** One queue out of many.
 *
 *  `waitingAssignedToMe` reads as a personal endpoint and is not one: it takes
 *  `pod_id` as a required query parameter and resolves the caller to a pod
 *  member before it looks anything up
 *  (`api/workflow_run_controller.py:149`). So "what is stuck on me" is a
 *  fan-out this side, over the pods already loaded for the rail — no extra
 *  request to learn which pods exist.
 *
 *  `allSettled`, because a RESTRICTED workflow in one pod must not empty the
 *  whole queue. The backend learned the same lesson inside this endpoint:
 *  authorizing per wait means one denial fails the entire request.
 */
async function gather(pods: Pod[], sample: boolean): Promise<{ rows: Row[]; unreadable: number }> {
    if (sample) {
        const { SAMPLE_WAITING, SAMPLE_WORKFLOWS } = await import("@/data/fixtures");
        const named = new Map(readWorkflows({ items: SAMPLE_WORKFLOWS }).map((one) => [one.id, one.name]));
        const first = pods[0];
        return {
            rows: byStuckLongest(readAssignments({ items: SAMPLE_WAITING })).map((one) => ({
                ...one,
                podId: first?.id ?? "sample",
                podName: first?.name ?? "Sample",
                workflowName: named.get(one.run.workflowId ?? "") ?? "A workflow",
            })),
            unreadable: 0,
        };
    }

    const answers = await Promise.allSettled(
        pods.map((pod) => lemma(pod.id).workflows.runs.waitingAssignedToMe({ limit: 50 })),
    );

    const owed: { pod: Pod; list: Assignment[] }[] = [];
    let unreadable = 0;
    answers.forEach((answer, index) => {
        if (answer.status !== "fulfilled") { unreadable += 1; return; }
        const list = readAssignments(answer.value);
        if (list.length > 0) owed.push({ pod: pods[index], list });
    });

    /* The run in an assignment is a summary, and a summary carries
       `workflow_id` and no name (`api/schemas.py:558`). So the names are
       fetched — but only for pods that actually owe something, which on a
       normal day is none of them and on a bad day is one. */
    const names = new Map<string, string>();
    await Promise.allSettled(owed.map(async ({ pod }) => {
        const listed = await lemma(pod.id).workflows.list({ limit: 100 });
        for (const flow of readWorkflows(listed)) names.set(flow.id, flow.name);
    }));

    const rows: Row[] = [];
    for (const { pod, list } of owed) {
        for (const one of byStuckLongest(list)) {
            rows.push({
                ...one,
                podId: pod.id,
                podName: pod.name,
                /* A name that could not be resolved is still a row: the form
                   is answerable without knowing what the workflow is called,
                   and dropping it would hide the work. */
                workflowName: names.get(one.run.workflowId ?? "") ?? "A workflow",
            });
        }
    }
    return { rows: byStuckLongest(rows), unreadable };
}
