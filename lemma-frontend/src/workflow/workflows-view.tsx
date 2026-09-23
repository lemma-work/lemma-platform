"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { source } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { BackIcon, ChatIcon, ChevronRightIcon, WarningIcon } from "@/ui/icons";
import {
    byNewest, readRunDetail, readRuns, readWorkflows, runMillis, runTone,
    sayCancelRefusal, sayFor, sayStatus, sayWhen, stillGoing,
    type StepRow, type WorkflowRow,
} from "./runs";
import { readShape, type FlowStep, type WorkflowShape } from "./shape";

/** What this teammate has been told to do the same way twice.
 *
 *  A whole backend module with nothing in this app pointing at it: workflows,
 *  their shape, their runs, and the step history that is the only record of
 *  what a run actually did.
 *
 *  Three depths in one place, because a workflow on its own says nothing —
 *  the list, one workflow, and one run. Flat rows on a divider at every depth,
 *  like the agents list and the connector catalogue: a pod has a handful of
 *  workflows, and a bordered box round each makes the eye cross a border to
 *  get from one name to the next.
 *
 *  Nothing here draws the graph. `workflows.visualize` returns an entire HTML
 *  debugging page rather than a graph format, so there is nothing to compose
 *  with — and at this size a picture would answer worse than the list does.
 *  "How does this run" is a question about order, and the answer to a question
 *  about order is a column you read downwards. The run history beneath it is
 *  already drawn that way; the two now stack on the same spine, so a failed
 *  node named in the history is findable by eye in the shape above it.
 *
 *  Nothing here edits the graph either, and that is the same decision the
 *  agents list makes: a workflow is changed by asking the teammate to change
 *  it. A form over `nodes`/`edges` would be a worse graph editor than the one
 *  this product does not want to build, and it would throw away the reason —
 *  the conversation is where "why does this branch on 5000" keeps its answer.
 */
export function WorkflowsView({ podId, teammate, onDiscuss }: {
    podId: string;
    teammate: string;
    /** Open the conversation bound to a workflow. Handed back rather than
     *  navigated to, because the shell owns which conversation is in front —
     *  the same wiring `onDiscussAgent` goes through. */
    onDiscuss?: (name: string) => void;
}) {
    const [openFlow, setOpenFlow] = useState<string | null>(null);
    const [openRun, setOpenRun] = useState<string | null>(null);

    const flows = useQuery({
        queryKey: ["workflows", podId],
        queryFn: async () => {
            if (source.label === "sample") {
                const { SAMPLE_WORKFLOWS } = await import("@/data/fixtures");
                return readWorkflows({ items: SAMPLE_WORKFLOWS });
            }
            return readWorkflows(await lemma(podId).workflows.list({ limit: 100 }));
        },
        staleTime: 5 * 60_000,
    });

    if (openRun && openFlow) {
        return <RunPane podId={podId} runId={openRun} workflowName={openFlow} onBack={() => setOpenRun(null)} />;
    }
    if (openFlow) {
        const flow = flows.data?.find((one) => one.name === openFlow);
        return (
            <RunsPane
                podId={podId}
                flow={flow ?? null}
                name={openFlow}
                teammate={teammate}
                onBack={() => setOpenFlow(null)}
                onOpenRun={setOpenRun}
                onDiscuss={onDiscuss}
            />
        );
    }

    return (
        <div className="wf-view">
            {flows.isPending && <p className="empty-row" role="status">Reading…</p>}
            {flows.isError && (
                <p className="empty-row" role="alert">
                    {isForbidden(flows.error)
                        ? "You may not list the workflows here."
                        : "Couldn’t load workflows."}{" "}
                    <button className="linkish" onClick={() => void flows.refetch()}>Try again</button>
                </p>
            )}
            {flows.isSuccess && flows.data.length === 0 && (
                <p className="empty-row">{teammate} has no workflows. Ask it to make one in the conversation.</p>
            )}

            {(flows.data?.length ?? 0) > 0 && (
                <div className="wf-list">
                    {flows.data?.map((flow) => (
                        <button className="wf-row" key={flow.id} onClick={() => setOpenFlow(flow.name)}>
                            <span className="wf-row__body">
                                <span className="wf-row__title">
                                    <strong>{flow.name}</strong>
                                    {/* Only when it is off. An "active" tag on
                                        every row is a tag that says nothing. */}
                                    {!flow.active && <i className="wf-tag">paused</i>}
                                </span>
                                <small>{flow.description || sayShape(flow)}</small>
                            </span>
                            <span className="wf-row__meta">{flow.steps} {flow.steps === 1 ? "step" : "steps"}</span>
                            <ChevronRightIcon size={16} />
                        </button>
                    ))}
                </div>
            )}

            {/* The same sentence the agents list ends on, for the same reason.
                Said once under the list rather than on every row: it is true
                of all of them, and a repeated instruction stops being read. */}
            {onDiscuss && (flows.data?.length ?? 0) > 0 && (
                <p className="wf-aside">A workflow is changed by asking {teammate} to change it.</p>
            )}
        </div>
    );
}

/** What a workflow is made of, when its author wrote no description.
 *
 *  `node_types` rides on the list response for exactly this
 *  (`api/schemas.py:444`), so it costs nothing — and "asks a person, then an
 *  agent" is a better answer than an empty line. */
function sayShape(flow: WorkflowRow): string {
    const kinds = new Set(flow.kinds);
    const parts: string[] = [];
    if (kinds.has("FORM")) parts.push("asks a person");
    if (kinds.has("AGENT")) parts.push("hands work to an agent");
    if (kinds.has("FUNCTION")) parts.push("runs a function");
    if (kinds.has("DECISION")) parts.push("branches");
    if (kinds.has("LOOP")) parts.push("loops");
    if (kinds.has("WAIT_UNTIL")) parts.push("waits until a set time");
    if (parts.length === 0) return "No description.";
    return parts.join(", ").replace(/^./, (one) => one.toUpperCase()) + ".";
}

/* ── one workflow: how it runs, then what happened when it did ─────── */

function RunsPane({ podId, flow, name, teammate, onBack, onOpenRun, onDiscuss }: {
    podId: string;
    flow: WorkflowRow | null;
    name: string;
    teammate: string;
    onBack: () => void;
    onOpenRun: (runId: string) => void;
    onDiscuss?: (name: string) => void;
}) {
    /* A second request, and unavoidable: the list response omits the graph on
       purpose and carries a `node_count` in its place (`api/schemas.py:444`).
       Its own query rather than a field on the row, so a forbidden or
       malformed graph costs the run history nothing — the two failures are
       separately true and separately said. */
    const shape = useQuery({
        queryKey: ["workflow-shape", podId, name],
        queryFn: async () => {
            if (source.label === "sample") {
                const { SAMPLE_WORKFLOW_SHAPES } = await import("@/data/fixtures");
                return readShape(SAMPLE_WORKFLOW_SHAPES[name] ?? null);
            }
            return readShape(await lemma(podId).workflows.get(name));
        },
        staleTime: 5 * 60_000,
    });

    const runs = useQuery({
        queryKey: ["workflow-runs", podId, name],
        queryFn: async () => {
            if (source.label === "sample") {
                const { SAMPLE_WORKFLOW_RUNS } = await import("@/data/fixtures");
                return byNewest(readRuns({ items: SAMPLE_WORKFLOW_RUNS[name] ?? [] }));
            }
            return byNewest(readRuns(await lemma(podId).workflows.runs.list(name, { limit: 50 })));
        },
        staleTime: 30_000,
        /* A run in flight changes without anybody touching this page, and a
           list of runs that never moves is a list people stop believing.
           Only while something is actually going. */
        refetchInterval: (query) => (query.state.data?.some((run) => stillGoing(run.status)) ? 15_000 : false),
    });

    return (
        <div className="wf-view">
            <Crumb onBack={onBack} title={name} note={flow?.description ?? undefined} />

            <Shape
                query={shape}
                teammate={teammate}
                onDiscuss={onDiscuss ? () => onDiscuss(name) : undefined}
            />

            {/* The history gets a heading now that something sits above it.
                Two lists on one spine with nothing between them read as one
                list that changes its mind halfway down. */}
            <h4 className="wf-heading">Run history</h4>

            {runs.isPending && <p className="empty-row" role="status">Reading…</p>}
            {runs.isError && (
                <p className="empty-row" role="alert">
                    {isForbidden(runs.error)
                        ? "You may not read this workflow’s runs."
                        : "Couldn’t load run history."}{" "}
                    <button className="linkish" onClick={() => void runs.refetch()}>Try again</button>
                </p>
            )}
            {runs.isSuccess && runs.data.length === 0 && <p className="empty-row">This has never run.</p>}

            {(runs.data?.length ?? 0) > 0 && (
                <div className="wf-list">
                    {runs.data?.map((run) => (
                        <button className="wf-row wf-row--run" key={run.id} onClick={() => onOpenRun(run.id)}>
                            <span className="wf-dot" data-tone={runTone(run.status)} aria-hidden="true" />
                            <span className="wf-row__body">
                                <span className="wf-row__title"><strong>{sayStatus(run.status)}</strong></span>
                                {/* The failed node is named in the row, not
                                    behind a click. A list of five failures is
                                    unreadable if you have to open each one to
                                    learn they all died on the same node. */}
                                <small>
                                    {run.failedNodeId
                                        ? "Failed at " + run.failedNodeId + (run.error ? " · " + run.error : "")
                                        : run.currentNodeId && stillGoing(run.status)
                                          ? "At " + run.currentNodeId
                                          : run.startType && run.startType !== "MANUAL"
                                            ? "Started by " + run.startType.toLowerCase()
                                            : "Started by hand"}
                                </small>
                            </span>
                            <span className="wf-row__meta">
                                {sayWhen(run.startedAt ?? run.createdAt) ?? "—"}
                                {sayFor(runMillis(run)) && <i> · {sayFor(runMillis(run))}</i>}
                            </span>
                            <ChevronRightIcon size={16} />
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}

/* ── how it runs ───────────────────────────────────────────────────── */

/** The shape of the thing, above the record of it doing the thing.
 *
 *  The heading on this section has always said "what it runs the same way
 *  twice" and the section has never shown what that is — a name, a sentence
 *  and a step *count*. This is the count opened up: the trigger, then every
 *  step in the order a run meets them.
 *
 *  Its own spine rather than the run history's, because the two are different
 *  claims. A step here is something that *can* happen; a step in the history
 *  is something that *did*, and colouring these by a status they do not have
 *  would be inventing one. So the marks are hollow and the tones are gone.
 */
function Shape({ query, teammate, onDiscuss }: {
    /* The query rather than the shape, because three of the four things this
       draws are states the shape cannot be in: still arriving, refused, and
       came back unreadable. A `shape | null` prop collapses all three into one
       blank. */
    query: UseQueryResult<WorkflowShape | null, Error>;
    teammate: string;
    onDiscuss?: () => void;
}) {
    const shape = query.data ?? null;

    return (
        <section className="wf-shape" aria-label="How it runs">
            <h4 className="wf-heading">How it runs</h4>

            {query.isPending && <p className="empty-row" role="status">Reading…</p>}
            {query.isError && (
                <p className="empty-row" role="alert">
                    {isForbidden(query.error)
                        ? "You may not read this workflow’s steps."
                        : "Couldn’t load workflow steps."}{" "}
                    <button className="linkish" onClick={() => void query.refetch()}>Try again</button>
                </p>
            )}
            {query.isSuccess && !shape && (
                <p className="empty-row" role="alert">This workflow came back in a shape this app could not read.</p>
            )}

            {shape && (
                <>
                    {/* The trigger sits on the same spine as the steps and one
                        mark above the first of them, because that is what it
                        is: the thing that happens before step one. Filled,
                        unlike the steps, so the column has a head. */}
                    <ol className="wf-spine">
                        <li className="wf-spine__row wf-spine__row--start">
                            <span className="wf-spine__mark" aria-hidden="true" />
                            <span className="wf-spine__body">
                                <span className="wf-spine__title"><b>{shape.start.says}</b></span>
                                {shape.start.detail.map((line, at) => (
                                    <span className="wf-spine__note" key={at}>{line}</span>
                                ))}
                            </span>
                        </li>
                        {shape.ordered.map((step, at) => <Step key={step.id || "step-" + at} step={step} />)}
                    </ol>

                    {shape.ordered.length === 0 && shape.orphans.length === 0 && (
                        <p className="empty-row">No steps yet — this workflow has a name and nothing in it.</p>
                    )}

                    {/* Above the orphans, not at the top: the trouble is
                        always *about* what is underneath it, and a warning
                        before the thing it warns about is a warning nobody can
                        check. */}
                    {shape.trouble && (
                        <p className="wf-shape__trouble" role="alert">
                            <WarningIcon size={15} aria-hidden="true" />
                            <span>{shape.trouble}</span>
                        </p>
                    )}

                    {shape.orphans.length > 0 && (
                        <>
                            {/* Named plainly, because an orphan is a real
                                finding. A step nothing reaches is work
                                somebody wrote and then wired past, and it will
                                sit there being nobody's fault forever unless
                                something says it is there. */}
                            <h5 className="wf-heading wf-heading--sub">Unconnected steps</h5>
                            <ol className="wf-spine wf-spine--loose">
                                {shape.orphans.map((step, at) => <Step key={step.id || "orphan-" + at} step={step} />)}
                            </ol>
                        </>
                    )}

                    {onDiscuss && (
                        <p className="wf-shape__ask">
                            <button className="btn wf-ask" onClick={onDiscuss}>
                                <ChatIcon size={15} aria-hidden="true" /><span>Ask {teammate} to change it</span>
                            </button>
                        </p>
                    )}
                </>
            )}
        </section>
    );
}

/** One step, as a row on the spine.
 *
 *  The id is set in the mono face the run history sets a step id in, and for
 *  the same reason: it is the string `failed_node_id` names when a run dies,
 *  so the two lists have to be readable against each other by eye.
 */
function Step({ step }: { step: FlowStep }) {
    return (
        <li className="wf-spine__row" data-kind={step.kind || undefined} data-broken={step.unreadable || undefined}>
            <span className="wf-spine__mark" aria-hidden="true" />
            <span className="wf-spine__body">
                <span className="wf-spine__title">
                    {step.id && <b>{step.id}</b>}
                    <i>{step.says}</i>
                    {/* Only when the author wrote one and it is not just the
                        id again. A label that repeats the id is two columns
                        of the same word. */}
                    {step.label && step.label !== step.id && <em>{step.label}</em>}
                </span>
                {step.detail.map((line, at) => (
                    <span className="wf-spine__note" key={at}>{line}</span>
                ))}
            </span>
        </li>
    );
}

/* ── one run ───────────────────────────────────────────────────────── */

function RunPane({ podId, runId, workflowName, onBack }: {
    podId: string;
    runId: string;
    workflowName: string;
    onBack: () => void;
}) {
    const cache = useQueryClient();
    const [refused, setRefused] = useState<string | null>(null);

    const run = useQuery({
        queryKey: ["workflow-run", podId, runId],
        queryFn: async () => {
            if (source.label === "sample") {
                const { SAMPLE_RUN_DETAIL } = await import("@/data/fixtures");
                return readRunDetail(SAMPLE_RUN_DETAIL[runId] ?? null);
            }
            return readRunDetail(await lemma(podId).workflows.runs.get(runId, podId));
        },
        staleTime: 15_000,
        refetchInterval: (query) => (query.state.data && stillGoing(query.state.data.status) ? 10_000 : false),
    });

    const cancel = useMutation({
        mutationFn: async () => {
            if (source.label === "sample") throw new Error("This is the sample source — connect a session to cancel anything.");
            return lemma(podId).workflows.runs.cancel(runId, podId);
        },
        onSuccess: () => {
            setRefused(null);
            void cache.invalidateQueries({ queryKey: ["workflow-run", podId, runId] });
            void cache.invalidateQueries({ queryKey: ["workflow-runs", podId] });
        },
        onError: (problem) => {
            const status = (problem as { statusCode?: number } | null)?.statusCode;
            setRefused(sayCancelRefusal(status, problem instanceof Error ? problem.message : "Couldn’t cancel this run."));
        },
    });

    const detail = run.data ?? null;

    return (
        <div className="wf-view">
            {/* No status in the crumb: the facts row directly beneath it
                already carries one, and "Waiting on an agent" printed twice
                eight pixels apart reads as two different facts that happen to
                agree. The crumb says where you are; the facts row says how it
                is going. */}
            <Crumb onBack={onBack} title={workflowName} />

            {run.isPending && <p className="empty-row" role="status">Reading…</p>}
            {run.isError && (
                <p className="empty-row" role="alert">
                    Couldn’t load this run.{" "}
                    <button className="linkish" onClick={() => void run.refetch()}>Try again</button>
                </p>
            )}
            {run.isSuccess && !detail && <p className="empty-row" role="alert">That run came back in a shape this app could not read.</p>}

            {detail && (
                <>
                    <div className="wf-run__facts">
                        <span className="wf-dot" data-tone={runTone(detail.status)} aria-hidden="true" />
                        <b>{sayStatus(detail.status, detail.wait?.type)}</b>
                        <span>{sayWhen(detail.startedAt ?? detail.createdAt) ?? "—"}</span>
                        {sayFor(runMillis(detail)) && <span>{sayFor(runMillis(detail))}</span>}
                        {stillGoing(detail.status) && (
                            <button
                                className="btn wf-run__cancel"
                                disabled={cancel.isPending}
                                onClick={() => cancel.mutate()}
                            >
                                {cancel.isPending ? "Cancelling…" : "Cancel"}
                            </button>
                        )}
                    </div>

                    {refused && <p className="wf-problem" role="alert">{refused}</p>}

                    {/* The failure, above the history rather than buried in
                        it. `failed_node_id` is only ever set on a FAILED run
                        (`api/schemas.py:490`), so a name here is the answer to
                        the only question anybody opens a failed run with. */}
                    {detail.failedNodeId && (
                        <p className="wf-run__failure" role="alert">
                            <WarningIcon size={15} aria-hidden="true" />
                            <span>
                                <b>{detail.failedNodeId}</b>
                                {detail.error ? " — " + detail.error : " failed, and said nothing about why."}
                            </span>
                        </p>
                    )}

                    <Steps steps={detail.steps} currentNodeId={detail.currentNodeId} />
                </>
            )}
        </div>
    );
}

/** What the run did, one node at a time.
 *
 *  A vertical list with a rule down it, the way the transcript reads: a run is
 *  a sequence of things that happened, and the shape that says "sequence" is a
 *  spine with marks on it. Not a graph — a graph shows what could happen; this
 *  shows what did.
 */
function Steps({ steps, currentNodeId }: { steps: StepRow[]; currentNodeId: string | null }) {
    if (steps.length === 0) {
        return <p className="empty-row">Nothing has run yet.</p>;
    }
    return (
        <ol className="wf-steps">
            {steps.map((step) => (
                <li
                    className="wf-step"
                    key={step.index + "-" + step.nodeId}
                    data-tone={stepTone(step.status)}
                    aria-current={step.nodeId && step.nodeId === currentNodeId ? "step" : undefined}
                >
                    <span className="wf-step__mark" aria-hidden="true" />
                    <span className="wf-step__body">
                        <span className="wf-step__title">
                            <b>{step.nodeId || "an unnamed step"}</b>
                            <i>{sayStepStatus(step.status)}</i>
                            {sayFor(stepMillis(step)) && <em>{sayFor(stepMillis(step))}</em>}
                        </span>
                        {step.error && <span className="wf-step__error">{step.error}</span>}
                        {sayOutput(step.output) && <pre className="wf-step__out">{sayOutput(step.output)}</pre>}
                    </span>
                </li>
            ))}
        </ol>
    );
}

function stepMillis(step: StepRow): number | null {
    if (!step.startedAt) return null;
    const from = Date.parse(step.startedAt);
    if (Number.isNaN(from)) return null;
    const to = step.completedAt ? Date.parse(step.completedAt) : Date.now();
    if (Number.isNaN(to)) return null;
    return to >= from ? to - from : null;
}

function stepTone(status: string): string {
    switch (status) {
        case "COMPLETED": return "good";
        case "FAILED": return "bad";
        case "WAITING": return "waiting";
        case "RUNNING": return "going";
        default: return "gone";
    }
}

function sayStepStatus(status: string): string {
    switch (status) {
        case "COMPLETED": return "done";
        case "FAILED": return "failed";
        case "WAITING": return "waiting";
        case "RUNNING": return "running";
        case "CANCELLED": return "cancelled";
        default: return "unknown";
    }
}

/** A step's output, printed.
 *
 *  `output_data` is `Any` on the wire and most of it is an object, so the
 *  honest rendering is JSON. Capped, because a function node can return a
 *  thousand rows and a step list is not a file viewer — and a truncated dump
 *  says "there is more" where an untruncated one just buries the next step.
 */
function sayOutput(value: unknown): string | null {
    if (value === null || value === undefined) return null;
    if (typeof value === "string") return value.trim() ? value.slice(0, 600) : null;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    try {
        const text = JSON.stringify(value, null, 2);
        if (!text || text === "{}" || text === "[]") return null;
        return text.length > 600 ? text.slice(0, 600) + "\n…" : text;
    } catch {
        /* A cycle, or a BigInt. Neither should reach here from JSON, and
           neither is worth taking the page down for. */
        return null;
    }
}

function Crumb({ onBack, title, note }: { onBack: () => void; title: string; note?: string }) {
    return (
        <div className="wf-crumb">
            <button className="wf-crumb__back" onClick={onBack}>
                <BackIcon size={15} aria-hidden="true" /><span>Back</span>
            </button>
            <span className="wf-crumb__title">{title}</span>
            {note && <span className="wf-crumb__note">{note}</span>}
        </div>
    );
}
