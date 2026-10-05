"use client";

import { useMemo } from "react";
import type { Pod } from "@/data";
import { useMe } from "@/session/use-me";
import { isForbidden } from "@/session/auth-state";
import { boardOf, sayHeldBy, type BoardCard, type BoardColumn } from "@/workflow/board";
import { sayFor, sayWhen } from "@/workflow/runs";
import type { WorkflowShape } from "@/workflow/shape";
import { useMyWaits, useRunsInFlight } from "@/workflow/use-run";
import { humanizeName } from "@/schedule/schedules";

const HOW: Record<string, string> = {
    SCHEDULED: "On a schedule",
    EVENT: "From an event",
    DATASTORE_EVENT: "From a row change",
};

/** Where every run still going is, one column per step.
 *
 *  Above the page's two columns rather than inside either: it is the answer to
 *  the question this page is opened with on a workflow that takes days, and
 *  the board needs the width — five steps side by side do not fit in a dock.
 *  Absent when nothing is going, because an empty board is a picture of the
 *  steps, and "How it runs" already draws those.
 */
export function RunBoard({ pod, workflowId, shape, onOpenRun, name }: {
    pod: Pod;
    workflowId: string | null;
    shape: WorkflowShape | null;
    name: string;
    onOpenRun: (runId: string, label: string) => void;
}) {
    const runs = useRunsInFlight(pod.id, workflowId);
    const waits = useMyWaits(pod.id);
    const me = useMe();
    const myMemberId = pod.members.find((member) => member.userId && member.userId === me)?.id ?? null;
    /* Waits that could not be read cost the board its "yours" marks and
       nothing else: every run is still drawn, at the step its summary names. */
    const board = useMemo(
        () => boardOf(shape, runs.data ?? [], workflowId ?? "", waits.data, myMemberId),
        [shape, runs.data, workflowId, waits.data, myMemberId],
    );

    if (runs.isError) {
        return (
            <p className="runpage__note wfboard__trouble" role="alert">
                {isForbidden(runs.error) ? "You may not see the runs still going here." : "Couldn’t load the runs still going."}{" "}
                <button className="linkish" onClick={() => void runs.refetch()}>Try again</button>
            </p>
        );
    }
    /* Before the shape lands every run would fall in the trailing column and
       then jump into place; waiting a moment longer is the calmer of the two. */
    if (!runs.isSuccess || !shape || !workflowId || board.count === 0) return null;

    const starter = (card: BoardCard) => {
        const { run } = card;
        if (run.startType && HOW[run.startType]) return HOW[run.startType];
        if (run.userId && run.userId === me) return "From you";
        return "From " + (pod.members.find((member) => member.userId === run.userId)?.name ?? "someone here");
    };
    /* The person a form is assigned to, by membership id — what a wait names.
       Only a form waits on a person; any other wait says what it is on. */
    const heldBy = (card: BoardCard, column: BoardColumn) => {
        if (card.yours) return "Waiting on you";
        const assignee = card.run.waitingOn?.assigneeId;
        const member = assignee ? pod.members.find((one) => one.id === assignee) : null;
        if (member) return member.userId && member.userId === me ? "Waiting on you" : "Waiting on " + member.name;
        return sayHeldBy(column.step, card.run);
    };

    return (
        <section className="wfboard" aria-label="In progress">
            <header className="wfboard__head">
                <h2>In progress</h2>
                <span>{board.count}</span>
                {board.mine > 0 && <em>{board.mine} waiting on you</em>}
            </header>
            <div className="wfboard__lanes">
                {board.columns.map((column) => (
                    <Lane key={column.id || "elsewhere"} column={column} starter={starter} heldBy={heldBy} onOpen={(runId) => onOpenRun(runId, name)} />
                ))}
            </div>
        </section>
    );
}

function Lane({ column, starter, heldBy, onOpen }: {
    column: BoardColumn;
    starter: (card: BoardCard) => string;
    heldBy: (card: BoardCard, column: BoardColumn) => string;
    onOpen: (runId: string) => void;
}) {
    const { step } = column;
    const title = step ? step.label || humanizeName(step.id) || "An unnamed step" : "Between steps";
    return (
        <section className="wfboard__lane" aria-label={title + ", " + column.cards.length} data-empty={column.cards.length === 0 || undefined}>
            <header>
                <b title={step?.id || undefined}>{title}</b>
                <span>{column.cards.length}</span>
            </header>
            {column.cards.length === 0 ? (
                <p className="wfboard__none">Nothing here</p>
            ) : (
                <ol>
                    {column.cards.map((card) => (
                        <li key={card.run.id}>
                            <button className="wfcard" data-mine={card.yours ? "" : undefined} onClick={() => onOpen(card.run.id)}>
                                {/* What the run is about when the workflow names its
                                    runs; who started it when it does not. */}
                                <span className="wfcard__top">
                                    <b title={card.run.title ? starter(card) : undefined}>{card.run.title ?? starter(card)}</b>
                                    <time dateTime={card.since ?? undefined}>{sayAge(card)}</time>
                                </span>
                                <small>{heldBy(card, column)}</small>
                            </button>
                        </li>
                    ))}
                </ol>
            )}
        </section>
    );
}

/** How long it has been at this step when that is known — a run's active
 *  wait carries its own time — and when it started otherwise, said as such,
 *  so a run's age is never passed off as time spent at the step. */
function sayAge(card: BoardCard): string {
    if (card.here && card.since) {
        const at = Date.parse(card.since);
        const said = Number.isNaN(at) ? null : sayFor(Date.now() - at);
        if (said) return said + " here";
    }
    return "started " + (sayWhen(card.since) ?? "—");
}
