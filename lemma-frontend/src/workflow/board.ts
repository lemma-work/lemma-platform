/** The runs still going, laid out along the steps they are stuck at.
 *
 *  A workflow that takes days and passes between people is asked one question
 *  more than any other — where is everything, and on whom — and a list sorted
 *  by when each run started cannot answer it: the answer is a count per step.
 *  So the columns are the workflow's own steps, in the order a run meets them,
 *  and each run still going sits in the column of the step it is on.
 *
 *  Not a status board. Status columns would put nearly every run in "Done"
 *  and the rest in "Waiting", which says nothing the run list does not; and
 *  nobody drags a card here, because a run moves when somebody answers it.
 *
 *  Only steps a run can sit at get a column. A decision or a loop hands on in
 *  the same advance it was entered (`execution/stepper.py` steps until a
 *  wait or the end), and an END is where a run stops being on the board at
 *  all, so a column for any of them would be permanently empty and would push
 *  the ones that matter off screen.
 */

import { runTone, stillGoing, type RunRow, type WaitRow } from "./runs";
import type { FlowStep, WorkflowShape } from "./shape";

/** Kinds a run can be parked at. An unreadable step ("") is kept: it is a
 *  step this workflow runs, and a run at it must still land somewhere. */
const HOLDS = new Set(["FORM", "AGENT", "FUNCTION", "WAIT_UNTIL", ""]);

export interface BoardCard {
    run: RunRow;
    /** The wait on this run assigned to the person looking, when the
     *  assigned-to-me list has it. */
    mine: WaitRow | null;
    /** Waiting on the person looking, by that list or by the run's own
     *  `waiting_on` assignee — the list is paged and can miss one. */
    yours: boolean;
    /** When it got here, as near as is known: the active wait's own time,
     *  the run's start when it has no wait. */
    since: string | null;
    /** Whether `since` is the time at this step rather than the run's start. */
    here: boolean;
}

export interface BoardColumn {
    /** The step id, or "" for the column of runs not at any known step. */
    id: string;
    step: FlowStep | null;
    cards: BoardCard[];
}

export interface Board {
    columns: BoardColumn[];
    /** Every run on the board. */
    count: number;
    /** How many of them are waiting on the person looking. */
    mine: number;
}

/** Group the runs still going by the step they are at.
 *
 *  `runs` may hold every run in the space and every status: the space-wide
 *  list is the only one that filters by status, so the caller passes what it
 *  has and this keeps only this workflow's runs that are still going.
 *
 *  A run whose current step is not a column — not started yet, at a step this
 *  shape does not know, or at a decision mid-advance — goes in a trailing
 *  column with no step rather than off the board. It is still going, and a
 *  board that drops it under-counts the one number it exists to show.
 */
export function boardOf(
    shape: Pick<WorkflowShape, "ordered" | "orphans"> | null,
    runs: RunRow[],
    workflowId: string,
    waits: ReadonlyMap<string, WaitRow> = new Map(),
    /** The viewer's pod-member id, what a wait's assignee is named by. */
    me: string | null = null,
): Board {
    const steps = [...(shape?.ordered ?? []), ...(shape?.orphans ?? [])].filter((step) => HOLDS.has(step.kind));
    const columns: BoardColumn[] = steps.map((step) => ({ id: step.id, step, cards: [] }));
    const at = new Map(columns.filter((column) => column.id).map((column) => [column.id, column]));
    const elsewhere: BoardColumn = { id: "", step: null, cards: [] };

    let count = 0;
    let mine = 0;
    for (const run of runs) {
        if (!stillGoing(run.status)) continue;
        if (run.workflowId !== workflowId) continue;
        const wait = waits.get(run.id) ?? null;
        const here = wait?.createdAt ?? run.waitingOn?.since ?? null;
        /* Yours by either source: the assigned-to-me list carries the full
           wait but is paged, the run's own summary names its assignee. */
        const yours = Boolean(wait) || Boolean(me && run.waitingOn?.assigneeId === me);
        const card: BoardCard = { run, mine: wait, yours, since: here ?? run.startedAt ?? run.createdAt, here: Boolean(here) };
        /* The wait names the step it is on, and it is the fresher of the two:
           a summary's `current_node_id` is whatever the last write left. */
        const column = at.get(wait?.nodeId ?? run.waitingOn?.nodeId ?? run.currentNodeId ?? "") ?? elsewhere;
        column.cards.push(card);
        count += 1;
        if (yours) mine += 1;
    }

    for (const column of [...columns, elsewhere]) column.cards.sort(byWaitingLongest);
    return { columns: elsewhere.cards.length ? [...columns, elsewhere] : columns, count, mine };
}

/** Yours first, then the longest stuck. Within a step the card at the top is
 *  the one somebody should look at, and a run waiting on you is that one
 *  whatever its age. */
function byWaitingLongest(left: BoardCard, right: BoardCard): number {
    if (left.yours !== right.yours) return left.yours ? -1 : 1;
    return stamp(left.since) - stamp(right.since);
}

function stamp(iso: string | null): number {
    const at = iso ? Date.parse(iso) : NaN;
    return Number.isNaN(at) ? Number.MAX_SAFE_INTEGER : at;
}

/** What a run at this step is waiting on: the wait's own kind when the run
 *  has one, the step's kind otherwise. A run that is merely RUNNING at a
 *  function is said as running, which it is. Naming the person a form waits
 *  on is the caller's, since only it holds the member list. */
export function sayHeldBy(step: FlowStep | null, run: RunRow): string {
    if (run.status === "PENDING") return "Starting";
    switch (run.waitingOn?.type) {
        case "HUMAN": return "Waiting on a person";
        case "AGENT": return "With an agent";
        case "FUNCTION": return "Running a function";
        case "TIME": return "Waiting for a set time";
    }
    switch (step?.kind) {
        case "FORM": return "Waiting on a person";
        case "AGENT": return "With an agent";
        case "FUNCTION": return "Running a function";
        case "WAIT_UNTIL": return "Waiting for a set time";
    }
    return runTone(run.status) === "waiting" ? "Waiting on a person" : "Running";
}
