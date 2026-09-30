"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { useMe } from "@/session/use-me";
import { WaitForm } from "@/workflow/form";
import { DataView } from "@/workflow/data-view";
import {
    readRun, runMillis, runTone, sayCancelRefusal, sayFor, sayStatus, sayWhen, stillGoing,
    type RunDetail, type StepRow,
} from "@/workflow/runs";
import {
    buildTree, idsIn, leadOf, progressOf, stateOfTrace, stateOfUnvisited, tracesByNode,
    type Arm, type Graph, type GraphNode, type NodeState, type TreeItem,
} from "@/workflow/run-tree";
import { useRun, useWorkflowGraph, useWorkflowList } from "@/workflow/use-run";
import { LiveConversation } from "@/thread/live-conversation";
import { AgentMark } from "./agent-mark";
import { isPodDefaultAgent } from "@/data/agent-names";
import {
    CheckIcon, ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon, ClockIcon, CloseIcon, CodeIcon, ExternalIcon,
    RefreshIcon, TreeIcon, UserIcon, WarningIcon, WorkflowIcon,
} from "@/ui/icons";

const STARTED: Record<string, string> = {
    MANUAL: "Started by hand",
    SCHEDULED: "Started on a schedule",
    EVENT: "Started by an event",
    DATASTORE_EVENT: "Started when a row changed",
};

interface Bot { name: string; label: string; iconUrl: string | null; front: boolean }

interface Ctx {
    pod: Pod;
    run: RunDetail;
    graph: Graph;
    traces: Map<string, StepRow[]>;
    bots: Map<string, Bot>;
    onOpenConversation: (id: string) => void;
    onChanged: () => void;
}

/** One workflow run, as a page.
 *
 *  Answers, top to bottom, what somebody opens a run to find out: which run
 *  and how it is doing; if it is stuck, on whom and what to do about it; and
 *  then every step in the shape of the workflow — the arm a decision took, the
 *  arm it did not, each pass of a loop — with what each step produced. An
 *  agent's step carries its conversation inline, because "what did it say" is
 *  the question and a link is one more place to go. */
export function RunPage({ pod, runId, label, onBack, onOpenConversation, onOpenRun, onOpenWorkflow, onNamed }: {
    pod: Pod;
    runId: string;
    label: string;
    onBack: () => void;
    onOpenConversation: (id: string) => void;
    onOpenRun: (runId: string, label: string) => void;
    onOpenWorkflow: (name: string) => void;
    /** The workflow's name, once the run has said which workflow it is. */
    onNamed?: (name: string) => void;
}) {
    const cache = useQueryClient();
    const sample = source.label === "sample";
    const me = useMe();
    const run = useRun(pod.id, runId);
    const flows = useWorkflowList(pod.id);
    const detail = run.data ?? null;
    const flow = detail ? flows.data?.find((one) => one.id === detail.workflowId) ?? null : null;
    const name = flow?.name ?? (label !== "Workflow run" ? label.replace(/ · run$/, "") : null);
    const shape = useWorkflowGraph(pod.id, name);
    const named = flow?.name ?? null;
    useEffect(() => {
        if (named) onNamed?.(named);
    }, [named, onNamed]);
    const bots = useQuery({
        queryKey: ["agents", pod.id],
        queryFn: () => source.listAgents(pod.id),
        staleTime: 5 * 60_000,
    });
    const botMap = useMemo(() => new Map((bots.data ?? []).map((bot) => [bot.name, { name: bot.name, label: bot.label, iconUrl: bot.iconUrl, front: bot.front }])), [bots.data]);

    const [confirming, setConfirming] = useState(false);
    const [refused, setRefused] = useState<string | null>(null);
    const refresh = () => void cache.invalidateQueries({ queryKey: ["workflow-run", pod.id, runId] });
    const cancel = useMutation({
        mutationFn: () => lemma(pod.id).workflows.runs.cancel(runId, pod.id),
        onSuccess: () => { setRefused(null); setConfirming(false); refresh(); },
        onError: (problem) => {
            const status = (problem as { statusCode?: number } | null)?.statusCode;
            setRefused(sayCancelRefusal(status, problem instanceof Error ? problem.message : "Couldn’t cancel this run."));
        },
    });
    const again = useMutation({
        mutationFn: async () => readRun(await lemma(pod.id).workflows.runs.create(name!)),
        onSuccess: (next) => { if (next) onOpenRun(next.id, name ?? label); },
        onError: (problem) => setRefused(problem instanceof Error ? problem.message : "Couldn’t start a new run."),
    });

    const graph = shape.data?.graph ?? null;
    const traces = useMemo(() => tracesByNode(detail?.steps ?? []), [detail?.steps]);
    const tree = useMemo(() => (graph ? buildTree(graph) : null), [graph]);
    const progress = graph && detail ? progressOf(graph, detail.steps) : null;
    const owner = detail?.userId
        ? detail.userId === me ? "you" : pod.members.find((member) => member.userId === detail.userId)?.name ?? null
        : null;
    const going = detail ? stillGoing(detail.status) : false;
    const nodeName = (id: string | null) => (id && graph?.nodes.get(id)?.label) || id || "a step";

    return (
        <div className="runpage">
            <nav className="runpage__crumb">
                <button onClick={() => (name ? onOpenWorkflow(name) : onBack())}>
                    <ChevronLeftIcon size={15} /> {name ?? "Back"}
                </button>
            </nav>

            <header className="runpage__head">
                <h1>
                    {name ?? label}
                    <span className="runpage__id">#{runId.slice(0, 8)}</span>
                </h1>
                {detail && (
                    <span className="runpage__status" data-tone={runTone(detail.status)}>
                        <i aria-hidden="true" />{sayStatus(detail.status, detail.wait?.type)}
                    </span>
                )}
                <span className="runpage__acts">
                    <button className="runpage__icon" title="Refresh" aria-label="Refresh" onClick={refresh}><RefreshIcon size={16} /></button>
                    {detail && going && !sample && (
                        <button className="runpage__cancel" disabled={cancel.isPending} onClick={() => setConfirming(true)}>Cancel run</button>
                    )}
                    {detail && !going && name && !sample && (
                        <button className="runpage__cancel" disabled={again.isPending} onClick={() => again.mutate()}>
                            {again.isPending ? "Starting…" : "Run again"}
                        </button>
                    )}
                </span>
            </header>
            {detail && (
                <p className="runpage__meta">
                    {[
                        /* "3 of 4" on a finished run reads as one missing; a
                           branch not taken is not a step left undone. */
                        progress && progress.total > 0
                            ? going ? progress.done + " of " + progress.total + " steps" : progress.done + (progress.done === 1 ? " step ran" : " steps ran")
                            : null,
                        ((detail.startType && STARTED[detail.startType]) || "Started") + (owner ? " by " + owner : ""),
                        sayWhen(detail.startedAt ?? detail.createdAt),
                        sayFor(runMillis(detail)) && (going ? "going for " : "took ") + sayFor(runMillis(detail)),
                    ].filter(Boolean).join(" · ")}
                </p>
            )}

            {confirming && (
                <div className="runpage__confirm" role="alertdialog" aria-label="Cancel this run">
                    <p>Cancel this run? Steps already done stay done; nothing after <b>{nodeName(detail?.currentNodeId ?? null)}</b> will run, and it cannot be resumed — only run again.</p>
                    <div>
                        <button className="runpage__danger" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? "Cancelling…" : "Cancel run"}</button>
                        <button className="runpage__cancel" onClick={() => setConfirming(false)}>Keep it running</button>
                    </div>
                </div>
            )}

            {run.isPending && <p className="runpage__note">Reading the run…</p>}
            {run.isError && <p className="runpage__note" role="alert">Couldn’t load this run. <button className="linkish" onClick={() => void run.refetch()}>Try again</button></p>}
            {run.isSuccess && !detail && <p className="runpage__note">This run can’t be read — it may have been removed, or you may not have access to it.</p>}
            {refused && <p className="runpage__problem" role="alert">{refused}</p>}

            {detail?.status === "FAILED" && (
                <div className="runpage__failure" role="alert">
                    <WarningIcon size={18} />
                    <div>
                        <b>Failed at {nodeName(detail.failedNodeId)}</b>
                        <p>{detail.error || "It stopped without saying why."}</p>
                    </div>
                </div>
            )}

            {detail && <Standing pod={pod} run={detail} graph={graph} bots={botMap} onOpenConversation={onOpenConversation} onChanged={refresh} />}

            {detail && (
                <section className="runpage__steps">
                    <h2>Steps</h2>
                    {shape.isPending && name && <p className="runpage__note">Reading the workflow…</p>}
                    {graph && tree ? (
                        <Items items={tree} ctx={{ pod, run: detail, graph, traces, bots: botMap, onOpenConversation, onChanged: refresh }} />
                    ) : !shape.isPending || !name ? (
                        /* Without the graph there is still the history. */
                        <ol className="rtree">
                            {detail.steps.map((step) => (
                                <FlatRow key={step.index} step={step} onOpenConversation={onOpenConversation} />
                            ))}
                        </ol>
                    ) : null}
                </section>
            )}

            {detail && Object.keys(detail.context).length > 0 && (
                <details className="runpage__input">
                    <summary>Run context</summary>
                    <pre>{JSON.stringify(detail.context, null, 2)}</pre>
                </details>
            )}
        </div>
    );
}

/* ── where it stands ───────────────────────────────────────────────── */

function Standing({ pod, run, graph, bots, onOpenConversation, onChanged }: {
    pod: Pod;
    run: RunDetail;
    graph: Graph | null;
    bots: Map<string, Bot>;
    onOpenConversation: (id: string) => void;
    onChanged: () => void;
}) {
    const wait = run.wait;
    const node = (wait?.nodeId && graph?.nodes.get(wait.nodeId)) || (run.currentNodeId && graph?.nodes.get(run.currentNodeId)) || null;
    const nodeLabel = node?.label || node?.id || wait?.nodeId || run.currentNodeId || "the next step";

    if (run.status === "COMPLETED") return <p className="standing standing--quiet"><CheckIcon size={16} /> Finished.</p>;
    if (run.status === "CANCELLED") return <p className="standing standing--quiet"><CloseIcon size={16} /> Stopped. Nothing after this point ran.</p>;
    if (!stillGoing(run.status)) return null;

    if (wait?.type === "HUMAN") {
        return (
            <section className="standing" data-tone="waiting">
                <header>
                    <span className="standing__dot" />
                    <div>
                        <b>Waiting on a person — {nodeLabel}</b>
                        <p>The run carries on as soon as this is submitted.</p>
                    </div>
                </header>
                {wait.schema && <WaitForm podId={pod.id} runId={run.id} wait={wait} onDone={onChanged} />}
            </section>
        );
    }
    if (wait?.type === "TIME") {
        return (
            <section className="standing">
                <header><ClockIcon size={16} /><div><b>Waiting for its time — {nodeLabel}</b><p>It picks up again on its own.</p></div></header>
            </section>
        );
    }
    const agent = wait?.agentName ?? (node?.kind === "AGENT" ? String(node.config.agent_name ?? "") : "");
    const who = agent ? bots.get(agent)?.label ?? agent : node?.kind === "FUNCTION" ? String(node.config.function_name ?? "A function") : null;
    return (
        <section className="standing" data-tone="going">
            <header>
                <span className="standing__dot" />
                <div>
                    <b>{who ? who + " is working" : "Working"} — {nodeLabel}</b>
                    <p>It carries on by itself; nothing is needed from you.</p>
                </div>
                {wait?.externalRef && (
                    <button className="runpage__link" onClick={() => onOpenConversation(wait.externalRef!)}>
                        Open conversation <ExternalIcon size={13} />
                    </button>
                )}
            </header>
        </section>
    );
}

/* ── the steps, in the workflow's shape ─────────────────────────────── */

function Items({ items, ctx }: { items: TreeItem[]; ctx: Ctx }) {
    return (
        <ol className="rtree">
            {items.map((item) => {
                if (item.type === "decision") return <Decision key={item.id} id={item.id} arms={item.arms} ctx={ctx} />;
                if (item.type === "loop") return <Loop key={item.id} id={item.id} body={item.body} ctx={ctx} />;
                return <NodeRows key={item.id} id={item.id} ctx={ctx} />;
            })}
        </ol>
    );
}

/** A node's rows: one per time it ran, so a loop's body shows each pass. */
function NodeRows({ id, ctx }: { id: string; ctx: Ctx }) {
    const node = ctx.graph.nodes.get(id);
    const traces = ctx.traces.get(id) ?? [];
    if (!node) return null;
    if (node.kind === "END") {
        const reached = traces.length > 0 || ctx.run.status === "COMPLETED";
        return (
            <li className="rtree__end" data-state={reached ? "done" : "pending"}>
                <span className="rtree__glyph" data-state={reached ? "done" : "pending"}>{reached && <CheckIcon size={11} />}</span>
                {reached ? "Finished" : "Finish"}
            </li>
        );
    }
    if (traces.length === 0) {
        return <Row node={node} trace={null} pass={null} state={stateOfUnvisited(stillGoing(ctx.run.status))} ctx={ctx} />;
    }
    return (
        <>
            {traces.map((trace, at) => (
                <Row key={trace.index} node={node} trace={trace} pass={traces.length > 1 ? at + 1 : null} state={stateOfTrace(trace.status)} ctx={ctx} />
            ))}
        </>
    );
}

function Decision({ id, arms, ctx }: { id: string; arms: Arm[]; ctx: Ctx }) {
    const ran = (ctx.traces.get(id) ?? []).length > 0;
    const takenAt = arms.findIndex((arm) => idsIn(arm.items).some((one) => (ctx.traces.get(one) ?? []).length > 0));
    const going = stillGoing(ctx.run.status);
    return (
        <>
            <NodeRows id={id} ctx={ctx} />
            <li className="rtree__arms">
                {arms.map((arm, at) => {
                    const taken = at === takenAt;
                    const settled = ran && takenAt >= 0;
                    return (
                        <div key={at} className="rtree__arm" data-taken={taken || undefined} data-dim={(settled && !taken) || (!ran && !going) || undefined}>
                            <p className="rtree__arm-label">
                                <TreeIcon size={13} />
                                <span>{arm.label}</span>
                                {arm.condition && <code>{arm.condition}</code>}
                                {settled && !taken && <em>not taken</em>}
                                {!settled && ran && going && <em>not decided yet</em>}
                            </p>
                            {arm.items.length > 0 && <Items items={arm.items} ctx={ctx} />}
                        </div>
                    );
                })}
            </li>
        </>
    );
}

function Loop({ id, body, ctx }: { id: string; body: TreeItem[]; ctx: Ctx }) {
    const node = ctx.graph.nodes.get(id);
    const over = node ? String(node.config.items_path ?? "") : "";
    /* A body of plain steps reads best pass by pass — look it up, record it;
       look up the next one — rather than every pass of one step, then every
       pass of the next. A body with its own branches keeps the nested form. */
    const flat = body.every((item) => item.type === "step");
    const passes = flat ? Math.max(0, ...body.map((item) => (ctx.traces.get(item.id) ?? []).length)) : 0;
    const going = stillGoing(ctx.run.status);
    return (
        <>
            <NodeRows id={id} ctx={ctx} />
            <li className="rtree__arms">
                <div className="rtree__arm" data-taken>
                    <p className="rtree__arm-label"><RefreshIcon size={13} /><span>For each item</span>{over && <code>{over}</code>}{flat && passes > 0 && <em>{passes} {passes === 1 ? "pass" : "passes"} so far</em>}</p>
                    {flat && passes > 0 ? (
                        <ol className="rtree">
                            {Array.from({ length: passes }, (_, pass) => pass).flatMap((pass) => (
                                body.map((item) => {
                                    const step = ctx.graph.nodes.get(item.id);
                                    if (!step) return null;
                                    const trace = (ctx.traces.get(item.id) ?? [])[pass] ?? null;
                                    const state = trace ? stateOfTrace(trace.status) : stateOfUnvisited(going && pass === passes - 1);
                                    return <Row key={item.id + ":" + pass} node={step} trace={trace} pass={passes > 1 ? pass + 1 : null} state={state} ctx={ctx} />;
                                })
                            ))}
                        </ol>
                    ) : <Items items={body} ctx={ctx} />}
                </div>
            </li>
        </>
    );
}

const STATE_WORD: Record<NodeState, string> = {
    done: "Done", failed: "Failed", running: "Running", waiting: "Waiting",
    skipped: "Not reached", pending: "Not yet", cancelled: "Cancelled",
};

function Actor({ node, ctx }: { node: GraphNode; ctx: Ctx }) {
    if (node.kind === "AGENT") {
        const agent = String(node.config.agent_name ?? "");
        const bot = ctx.bots.get(agent);
        return (
            <span className="rtree__actor rtree__actor--bot">
                <AgentMark pod={ctx.pod} agent={bot ?? { name: agent, label: agent, front: isPodDefaultAgent(agent), iconUrl: null }} size={26} />
            </span>
        );
    }
    const icon = node.kind === "FORM" ? <UserIcon size={15} />
        : node.kind === "FUNCTION" ? <CodeIcon size={15} />
        : node.kind === "DECISION" ? <TreeIcon size={15} />
        : node.kind === "LOOP" ? <RefreshIcon size={15} />
        : node.kind === "WAIT_UNTIL" ? <ClockIcon size={15} />
        : <WorkflowIcon size={15} />;
    return <span className="rtree__actor" data-kind={node.kind}>{icon}</span>;
}

function whoOf(node: GraphNode, ctx: Ctx): string {
    switch (node.kind) {
        case "AGENT": {
            const agent = String(node.config.agent_name ?? "");
            return ctx.bots.get(agent)?.label ?? (agent || "An agent");
        }
        case "FUNCTION": return String(node.config.function_name ?? "A function");
        case "FORM": return "A person";
        case "DECISION": return "Decision";
        case "LOOP": return "Repeat";
        case "WAIT_UNTIL": return "Wait";
        default: return node.kind.charAt(0) + node.kind.slice(1).toLowerCase();
    }
}

function Row({ node, trace, pass, state, ctx }: { node: GraphNode; trace: StepRow | null; pass: number | null; state: NodeState; ctx: Ctx }) {
    const [open, setOpen] = useState(state === "failed" || state === "running" || state === "waiting");
    const openable = Boolean(trace);
    const millis = trace?.startedAt
        ? runMillis({ startedAt: trace.startedAt, createdAt: trace.startedAt, completedAt: trace.completedAt })
        : null;
    return (
        <li className="rtree__row" data-state={state}>
            <button className="rtree__head" disabled={!openable} aria-expanded={openable ? open : undefined} onClick={() => setOpen((v) => !v)}>
                <Actor node={node} ctx={ctx} />
                <span className="rtree__who">
                    <b>{whoOf(node, ctx)}</b>
                    <small>{node.label || node.id}</small>
                </span>
                {pass && <em className="rtree__pass">Pass {pass}</em>}
                <span className="rtree__state"><StateGlyph state={state} />{STATE_WORD[state]}</span>
                <span className="rtree__time">{millis !== null ? sayFor(millis) + (state === "running" || state === "waiting" ? " so far" : "") : ""}</span>
                {openable ? (open ? <ChevronDownIcon size={14} /> : <ChevronRightIcon size={14} />) : <i className="rtree__chev-space" />}
            </button>
            {open && trace && <Body node={node} trace={trace} ctx={ctx} />}
        </li>
    );
}

function StateGlyph({ state }: { state: NodeState }) {
    return <span className="rtree__glyph" data-state={state}>
        {state === "done" ? <CheckIcon size={11} /> : state === "failed" ? <CloseIcon size={11} /> : null}
    </span>;
}

function Body({ node, trace, ctx }: { node: GraphNode; trace: StepRow; ctx: Ctx }) {
    const wait = ctx.run.wait;
    const waitingHere = Boolean(wait && wait.nodeId === node.id && stillGoing(ctx.run.status));
    const conversation = trace.externalRef ?? (waitingHere ? wait?.externalRef ?? null : null);
    const output = trace.output;
    const lead = leadOf(output);
    return (
        <div className="rtree__body">
            {trace.error && <p className="rtree__error">{trace.error}</p>}

            {node.kind === "AGENT" && (
                <>
                    {lead && <p className="rtree__lead">{lead}</p>}
                    {conversation && (
                        <div className="rtree__chat">
                            <div className="rtree__chat-bar">
                                <span>Conversation with {whoOf(node, ctx)}</span>
                                <button className="runpage__link" onClick={() => ctx.onOpenConversation(conversation)}>
                                    Open <ExternalIcon size={13} />
                                </button>
                            </div>
                            {source.label === "live" ? (
                                <div className="rtree__chat-frame convo-host">
                                    <LiveConversation pod={ctx.pod} conversationId={conversation} placeholder={"Message " + whoOf(node, ctx) + "…"} />
                                </div>
                            ) : (
                                <p className="rtree__chat-empty">The conversation shows here once you are signed in.</p>
                            )}
                        </div>
                    )}
                    <DataView value={output} lead={lead} />
                </>
            )}

            {node.kind === "FORM" && (
                waitingHere && wait?.type === "HUMAN" && wait.schema
                    ? <WaitForm podId={ctx.pod.id} runId={ctx.run.id} wait={wait} onDone={ctx.onChanged} />
                    : <DataView value={output} />
            )}

            {node.kind === "DECISION" && <p className="rtree__lead">{decisionSays(output)}</p>}

            {node.kind !== "AGENT" && node.kind !== "FORM" && node.kind !== "DECISION" && <DataView value={output} lead={lead} showLead />}
        </div>
    );
}

function decisionSays(output: unknown): string {
    const matched = output && typeof output === "object" ? (output as Record<string, unknown>).matched_condition : undefined;
    if (typeof matched === "string" && matched) return "Matched " + matched + ".";
    if (matched === null) return "No rule matched, so it took the default path.";
    return "Decided.";
}

/** A history row when the workflow's graph could not be read. */
function FlatRow({ step, onOpenConversation }: { step: StepRow; onOpenConversation: (id: string) => void }) {
    const state = stateOfTrace(step.status);
    return (
        <li className="rtree__row" data-state={state}>
            <div className="rtree__head">
                <span className="rtree__actor"><WorkflowIcon size={15} /></span>
                <span className="rtree__who"><b>{step.nodeId || "Unnamed step"}</b></span>
                <span className="rtree__state"><StateGlyph state={state} />{STATE_WORD[state]}</span>
                {step.externalRef && <button className="runpage__link" onClick={() => onOpenConversation(step.externalRef!)}>Conversation</button>}
            </div>
            {(step.error || step.output !== undefined) && (
                <div className="rtree__body">
                    {step.error && <p className="rtree__error">{step.error}</p>}
                    <DataView value={step.output} lead={leadOf(step.output)} showLead />
                </div>
            )}
        </li>
    );
}
