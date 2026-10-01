"use client";

import { runMillis, runTone, sayFor, sayStatus, sayWhen, stillGoing, type RunRow } from "./runs";
import { ChevronRightIcon } from "@/ui/icons";

const HOW: Record<string, string> = {
    MANUAL: "by hand",
    SCHEDULED: "on a schedule",
    EVENT: "by an event",
    DATASTORE_EVENT: "when a row changed",
};

/** What a run is doing, in one line: where it died, where it is, or how it
 *  started. The failed step is named in the row rather than behind a click —
 *  five failures on the same step read as one problem only when you can see
 *  it five times. Named by its label when the caller has the steps, so the
 *  row says "At Dev approves the fix" beside a list that says the same. */
export function sayRunLine(run: RunRow, names?: ReadonlyMap<string, string>): string {
    const step = (id: string) => names?.get(id) ?? id;
    if (run.failedNodeId) return "Failed at " + step(run.failedNodeId) + (run.error ? ": " + run.error : "");
    if (stillGoing(run.status) && run.currentNodeId) return "At " + step(run.currentNodeId);
    return "Started " + (HOW[run.startType ?? "MANUAL"] ?? "by hand");
}

/** One run as a row. `workflow` is shown when the list spans workflows. */
export function RunRowButton({ run, workflow, names, onOpen }: { run: RunRow; workflow?: string | null; names?: ReadonlyMap<string, string>; onOpen: () => void }) {
    const took = sayFor(runMillis(run));
    return (
        <button className="runrow" onClick={onOpen} data-live={stillGoing(run.status) || undefined}>
            <span className="runrow__dot" data-tone={runTone(run.status)} aria-hidden="true" />
            <span className="runrow__body">
                <span className="runrow__title">
                    {workflow ? <b>{workflow}</b> : <b>{sayStatus(run.status)}</b>}
                    {workflow && <em data-tone={runTone(run.status)}>{sayStatus(run.status)}</em>}
                </span>
                <small>{sayRunLine(run, names)}</small>
            </span>
            <span className="runrow__when">
                {sayWhen(run.startedAt ?? run.createdAt) ?? "—"}
                {took && <small>{stillGoing(run.status) ? "going " : ""}{took}</small>}
            </span>
            <ChevronRightIcon size={15} />
        </button>
    );
}
