import { useMemo, useState, type ReactNode } from "react";
import { View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useLemma } from "../../data/hooks";
import { routes } from "../../data/routes";
import { isPodDefaultAgent } from "@web/data/agent-names";
import { Glyph } from "../../thread/glyphs";
import { Loading } from "../../ui/chrome";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Num, Text } from "../../ui/text";
import { color, font } from "../../ui/tokens";
import { ConfirmSheet } from "../tables/bits";
import { useAgents, useMembers, useMyId, useRun, useRunActions, useStartRun, useWorkflow, useWorkflows } from "./hooks";
import { personOf, runMeta } from "./model";
import { Data, Fields, Heading, Note, RunEnd, STATE_WORD, StateMark, StepGlyph, Trouble } from "./parts";
import { workRoutes } from "./routes";
import { Screen } from "./screen";
import { WaitForm } from "./wait-form";
import type { AgentRow } from "@web/data/agents";
import { buildTree, idsIn, leadOf, progressOf, sayDecided, stateOfTrace, stateOfUnvisited, tracesByNode, type Arm, type Graph, type GraphNode, type NodeState, type TreeItem } from "@web/workflow/run-tree";
import { canRetryFrom, runMillis, runTone, sayCancelRefusal, sayFor, sayRetryRefusal, sayStatus, stillGoing, type RunDetail, type StepRow } from "@web/workflow/runs";
import { humanizeName } from "@web/schedule/schedules";

interface Ctx {
    podId: string;
    run: RunDetail;
    graph: Graph;
    traces: Map<string, StepRow[]>;
    agents: Map<string, AgentRow>;
    lemmaName: string;
}

/** One run (`space/run-page.tsx`): which run and how it is doing; if it is
 *  stuck, on whom and what to do about it (the form, answered here); then
 *  every step in the shape of the workflow, the arm a decision took and the
 *  one it did not, each pass of a loop, with what each step produced. An
 *  agent's step opens its chat in the chat screen. */
export function RunScreen() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string; id: string; back?: string }>();
    const podId = String(params.podId);
    const runId = String(params.id);
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "the Lemma";
    const me = useMyId();
    const run = useRun(podId, runId);
    const flows = useWorkflows(podId);
    const detail = run.data ?? null;
    const flow = detail ? flows.data?.find((one) => one.id === detail.workflowId) ?? null : null;
    const name = flow?.name ?? null;
    const read = useWorkflow(podId, name);
    const agents = useAgents(podId, lemmaName);
    const members = useMembers(podId);
    const actions = useRunActions(podId, runId);
    const again = useStartRun(podId);
    const [confirming, setConfirming] = useState(false);
    const [refused, setRefused] = useState<string | null>(null);

    const graph = read.graph;
    const traces = useMemo(() => tracesByNode(detail?.steps ?? []), [detail?.steps]);
    const tree = useMemo(() => (graph ? buildTree(graph) : null), [graph]);
    const progress = graph && detail ? progressOf(graph, detail.steps) : null;
    const agentMap = useMemo(() => new Map((agents.data ?? []).map((one) => [one.name, one])), [agents.data]);
    const going = detail ? stillGoing(detail.status) : false;
    const nodeName = (id: string | null) => (id && graph?.nodes.get(id)?.label) || id || "a step";
    const retryable = detail?.status === "FAILED" && canRetryFrom(detail.failedNodeId ? graph?.nodes.get(detail.failedNodeId)?.kind : null);
    const owner = detail ? personOf(detail.userId, me, members.data ?? []) : null;
    const status = (problem: unknown) => (problem as { statusCode?: number } | null)?.statusCode;

    const cancel = () => actions.cancel.mutate(undefined, {
        onSuccess: () => { setRefused(null); setConfirming(false); },
        onError: (problem) => { setConfirming(false); setRefused(sayCancelRefusal(status(problem), problem instanceof Error ? problem.message : "Couldn’t cancel this run.")); },
    });
    const retry = () => actions.retry.mutate(undefined, {
        onSuccess: () => setRefused(null),
        onError: (problem) => setRefused(sayRetryRefusal(status(problem), problem instanceof Error ? problem.message : "Couldn’t retry this run.")),
    });
    const runAgain = () => {
        if (!name) return;
        again.mutate(name, {
            onSuccess: (made) => { if (made) router.replace(workRoutes.run(podId, made.id) as never); },
            onError: (problem) => setRefused(problem instanceof Error ? problem.message : "Couldn’t start a new run."),
        });
    };

    const back = params.back ? String(params.back) : name ? humanizeName(name) : lemmaName;
    const tone = detail ? runTone(detail.status) : "gone";
    return (
        <Screen podId={podId} back={back} onRefresh={() => run.refetch()}>
            <View style={{ gap: 6, paddingTop: 6, paddingHorizontal: 4 }}>
                <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24, lineHeight: 29 }}>
                    {detail?.title ?? (name ? humanizeName(name) : "Workflow run")}
                    <Text size={14} tone={color.ink3} style={{ fontFamily: font.num }}>{"  #" + runId.slice(0, 8)}</Text>
                </Text>
                {detail?.title && name ? <Text size={14.5} tone={color.ink2}>{humanizeName(name)}</Text> : null}
                {detail ? (
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        <RunEnd mark={tone === "going" ? "going" : tone === "bad" ? "failed" : tone === "waiting" ? "yours" : "quiet"} />
                        <Text size={15} tone={tone === "bad" ? color.amber : tone === "going" || tone === "waiting" ? color.violetInk : color.ink2}>{sayStatus(detail.status, detail.wait?.type)}</Text>
                    </View>
                ) : null}
                {detail ? <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19 }}>{runMeta(detail, progress, owner)}</Text> : null}
                {detail ? (
                    <View style={{ flexDirection: "row", gap: 8, paddingTop: 6 }}>
                        {going ? <Button title="Cancel run" kind="outline" disabled={actions.cancel.isPending} onPress={() => setConfirming(true)} /> : null}
                        {!going && name ? <Button title={again.isPending ? "Starting…" : "Run again"} kind="outline" busy={again.isPending} onPress={runAgain} /> : null}
                    </View>
                ) : null}
            </View>

            {run.isPending ? <Loading label="Reading the run" /> : null}
            {run.isError ? <Trouble text="Couldn’t read this run." onRetry={() => void run.refetch()} /> : null}
            {run.isSuccess && !detail ? <Note>This run can’t be read. It may have been removed, or you may not have access to it.</Note> : null}
            {refused ? <Trouble text={refused} /> : null}

            {detail?.status === "FAILED" ? (
                <View accessibilityRole="alert" style={{ marginTop: 14, padding: 14, borderRadius: 14, backgroundColor: color.amberWash, gap: 6 }}>
                    <Text size={15} weight="medium" tone={color.amber}>Failed at {nodeName(detail.failedNodeId)}</Text>
                    <Text size={14.5} tone={color.amber} selectable style={{ lineHeight: 20 }}>{detail.error || "It stopped without saying why."}</Text>
                    {retryable ? (
                        /* Every step before this one keeps its result, so an
                           agent's answer is not paid for twice. */
                        <View style={{ flexDirection: "row", paddingTop: 4 }}>
                            <Button title={actions.retry.isPending ? "Retrying…" : "Retry from this step"} kind="outline" busy={actions.retry.isPending} onPress={retry} />
                        </View>
                    ) : null}
                </View>
            ) : null}

            {detail ? <Standing podId={podId} run={detail} graph={graph} agents={agentMap} lemmaName={lemmaName} /> : null}

            {detail ? (
                <View>
                    <Heading>Steps</Heading>
                    {flows.isPending || (read.query.isPending && name) ? <Loading label="Reading the workflow" /> : graph && tree ? (
                        <Items items={tree} ctx={{ podId, run: detail, graph, traces, agents: agentMap, lemmaName }} />
                    ) : (
                        /* Without the workflow there is still the history. */
                        detail.steps.map((step) => <FlatRow key={step.index} podId={podId} step={step} />)
                    )}
                </View>
            ) : null}

            {detail && Object.keys(detail.context).length > 0 ? <Context value={detail.context} /> : null}

            <ConfirmSheet
                open={confirming}
                onClose={() => setConfirming(false)}
                title="Cancel this run"
                say={`Steps already done stay done. Nothing after ${nodeName(detail?.currentNodeId ?? null)} will run, and it cannot be picked up again, only run again.`}
                yes="Cancel run"
                busy={actions.cancel.isPending}
                onYes={cancel}
            />
        </Screen>
    );
}

/* ── where it stands ─────────────────────────────────────────────────── */

function whoIs(agent: string, agents: Map<string, AgentRow>, lemmaName: string): string {
    if (isPodDefaultAgent(agent)) return lemmaName;
    return agents.get(agent)?.label ?? (agent || "An agent");
}

function Standing({ podId, run, graph, agents, lemmaName }: { podId: string; run: RunDetail; graph: Graph | null; agents: Map<string, AgentRow>; lemmaName: string }) {
    const router = useRouter();
    const wait = run.wait;
    const node = (wait?.nodeId && graph?.nodes.get(wait.nodeId)) || (run.currentNodeId && graph?.nodes.get(run.currentNodeId)) || null;
    const nodeLabel = node?.label || node?.id || wait?.nodeId || run.currentNodeId || "the next step";
    const box = (children: ReactNode, hot = false) => (
        <View style={{ marginTop: 14, padding: 14, borderRadius: 14, borderWidth: hot ? 0 : 1, borderColor: color.line2, backgroundColor: hot ? color.violetWash : color.paper, gap: 6 }}>{children}</View>
    );

    if (run.status === "COMPLETED") return <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 14, paddingHorizontal: 4 }}><StateMark state="done" /><Text size={15} tone={color.ink2}>Finished.</Text></View>;
    if (run.status === "CANCELLED") return <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 14, paddingHorizontal: 4 }}><StateMark state="cancelled" /><Text size={15} tone={color.ink2}>Stopped. Nothing after this point ran.</Text></View>;
    if (!stillGoing(run.status)) return null;

    if (wait?.type === "HUMAN") {
        return box(
            <>
                <Text size={15.5} weight="medium" tone={color.violetInk}>Waiting on a person: {nodeLabel}</Text>
                <Text size={14} tone={color.ink2}>The run carries on as soon as this is sent.</Text>
                {wait.schema ? <View style={{ paddingTop: 8 }}><WaitForm podId={podId} runId={run.id} wait={wait} /></View> : null}
            </>,
            true,
        );
    }
    if (wait?.type === "TIME") return box(<><Text size={15.5} weight="medium">Waiting for its time: {nodeLabel}</Text><Text size={14} tone={color.ink2}>It picks up again on its own.</Text></>);
    if (wait?.type === "DECISION") {
        return box(<><Text size={15.5} weight="medium">Weighing a question: {nodeLabel}</Text><Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>{wait.question ? "“" + wait.question + "” " : ""}It goes on down the branch the answer picks; nothing is needed from you.</Text></>);
    }
    const agent = wait?.agentName ?? (node?.kind === "AGENT" ? String(node.config.agent_name ?? "") : "");
    const who = agent ? whoIs(agent, agents, lemmaName) : node?.kind === "FUNCTION" ? String(node.config.function_name ?? "A function") : null;
    return box(
        <>
            <Text size={15.5} weight="medium">{who ? who + " is working" : "Working"}: {nodeLabel}</Text>
            <Text size={14} tone={color.ink2}>It carries on by itself; nothing is needed from you.</Text>
            {wait?.externalRef ? <View style={{ flexDirection: "row", paddingTop: 4 }}><Button title="Open the chat" kind="outline" height={36} size={14} onPress={() => router.push(routes.chat(podId, wait.externalRef!) as never)} /></View> : null}
        </>,
    );
}

/* ── the steps, in the workflow's shape ──────────────────────────────── */

function Items({ items, ctx, depth = 0 }: { items: TreeItem[]; ctx: Ctx; depth?: number }) {
    return (
        <View>
            {items.map((item) => {
                if (item.type === "decision") return <Decision key={item.id} id={item.id} arms={item.arms} ctx={ctx} depth={depth} />;
                if (item.type === "loop") return <Loop key={item.id} id={item.id} body={item.body} ctx={ctx} depth={depth} />;
                return <NodeRows key={item.id} id={item.id} ctx={ctx} />;
            })}
        </View>
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
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 40, paddingHorizontal: 4 }}>
                <View style={{ width: 22, alignItems: "center" }}><StateMark state={reached ? "done" : "pending"} /></View>
                <Text size={14.5} tone={color.ink3}>{reached ? "Finished" : "Finish"}</Text>
            </View>
        );
    }
    if (traces.length === 0) return <StepLine node={node} trace={null} pass={null} state={stateOfUnvisited(stillGoing(ctx.run.status))} ctx={ctx} />;
    return <>{traces.map((trace, at) => <StepLine key={trace.index} node={node} trace={trace} pass={traces.length > 1 ? at + 1 : null} state={stateOfTrace(trace.status)} ctx={ctx} />)}</>;
}

function Indent({ children, depth }: { children: ReactNode; depth: number }) {
    return <View style={{ marginLeft: depth > 3 ? 0 : 14, paddingLeft: 10, borderLeftWidth: 1, borderLeftColor: color.line2 }}>{children}</View>;
}

function Decision({ id, arms, ctx, depth }: { id: string; arms: Arm[]; ctx: Ctx; depth: number }) {
    const ran = (ctx.traces.get(id) ?? []).length > 0;
    const takenAt = arms.findIndex((arm) => idsIn(arm.items).some((one) => (ctx.traces.get(one) ?? []).length > 0));
    const going = stillGoing(ctx.run.status);
    return (
        <>
            <NodeRows id={id} ctx={ctx} />
            <Indent depth={depth}>
                {arms.map((arm, at) => {
                    const taken = at === takenAt;
                    const settled = ran && takenAt >= 0;
                    const dim = (settled && !taken) || (!ran && !going);
                    return (
                        <View key={at} style={{ opacity: dim ? 0.55 : 1 }}>
                            <View style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingTop: 8, paddingBottom: 2, paddingHorizontal: 4 }}>
                                <Glyph name="tree" size={13} tone={color.ink3} />
                                <Text size={13.5} tone={color.ink2} numberOfLines={2} style={{ flex: 1 }}>
                                    {arm.label}
                                    {arm.condition ? <Text size={12} tone={color.ink3} style={{ fontFamily: font.num }}>{"  " + arm.condition}</Text> : null}
                                </Text>
                                {settled && !taken ? <Text size={12.5} tone={color.ink3}>not taken</Text> : !settled && ran && going ? <Text size={12.5} tone={color.ink3}>not decided yet</Text> : null}
                            </View>
                            {arm.items.length > 0 ? <Items items={arm.items} ctx={ctx} depth={depth + 1} /> : null}
                        </View>
                    );
                })}
            </Indent>
        </>
    );
}

function Loop({ id, body, ctx, depth }: { id: string; body: TreeItem[]; ctx: Ctx; depth: number }) {
    const node = ctx.graph.nodes.get(id);
    const over = node ? String(node.config.items_path ?? "") : "";
    /* A body of plain steps reads best pass by pass; one with its own
       branches keeps the nested form. */
    const flat = body.every((item) => item.type === "step");
    const passes = flat ? Math.max(0, ...body.map((item) => (ctx.traces.get(item.id) ?? []).length)) : 0;
    const going = stillGoing(ctx.run.status);
    return (
        <>
            <NodeRows id={id} ctx={ctx} />
            <Indent depth={depth}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingTop: 8, paddingBottom: 2, paddingHorizontal: 4 }}>
                    <Icon name="refresh" size={13} tone={color.ink3} />
                    <Text size={13.5} tone={color.ink2} style={{ flex: 1 }}>
                        For each item
                        {over ? <Text size={12} tone={color.ink3} style={{ fontFamily: font.num }}>{"  " + over}</Text> : null}
                    </Text>
                    {flat && passes > 0 ? <Text size={12.5} tone={color.ink3}><Num size={12.5} tone={color.ink3}>{passes}</Num> {passes === 1 ? "pass" : "passes"} so far</Text> : null}
                </View>
                {flat && passes > 0 ? (
                    Array.from({ length: passes }, (_, pass) => pass).flatMap((pass) => body.map((item) => {
                        const step = ctx.graph.nodes.get(item.id);
                        if (!step) return null;
                        const trace = (ctx.traces.get(item.id) ?? [])[pass] ?? null;
                        const state = trace ? stateOfTrace(trace.status) : stateOfUnvisited(going && pass === passes - 1);
                        return <StepLine key={item.id + ":" + pass} node={step} trace={trace} pass={passes > 1 ? pass + 1 : null} state={state} ctx={ctx} />;
                    }))
                ) : <Items items={body} ctx={ctx} depth={depth + 1} />}
            </Indent>
        </>
    );
}

function whoOf(node: GraphNode, ctx: Ctx): string {
    switch (node.kind) {
        case "AGENT": return whoIs(String(node.config.agent_name ?? ""), ctx.agents, ctx.lemmaName);
        case "FUNCTION": return String(node.config.function_name ?? "A function");
        case "FORM": return "A person";
        case "DECISION": return "Decision";
        case "LOOP": return "Repeat";
        case "WAIT_UNTIL": return "Wait";
        default: return node.kind.charAt(0) + node.kind.slice(1).toLowerCase();
    }
}

/** One step, one line: what it is, who does it, how it stands, how long.
 *  Opens to what it produced; a step still going, waiting or failed opens by
 *  itself. */
function StepLine({ node, trace, pass, state, ctx }: { node: GraphNode; trace: StepRow | null; pass: number | null; state: NodeState; ctx: Ctx }) {
    const [open, setOpen] = useState(state === "failed" || state === "running" || state === "waiting");
    const millis = trace?.startedAt ? runMillis({ startedAt: trace.startedAt, createdAt: trace.startedAt, completedAt: trace.completedAt }) : null;
    const took = millis !== null ? sayFor(millis) : null;
    const label = node.label || node.id;
    const who = whoOf(node, ctx);
    return (
        <View style={{ borderBottomWidth: 1, borderBottomColor: color.line }}>
            <Press
                label={`${label}, ${who}${pass ? `, pass ${pass}` : ""}. ${STATE_WORD[state]}${took ? `, ${took}` : ""}`}
                accessibilityState={trace ? { expanded: open } : undefined}
                disabled={!trace}
                onPress={() => setOpen((was) => !was)}
                style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 48, paddingHorizontal: 4 }}
            >
                <View style={{ width: 22, alignItems: "center", opacity: state === "skipped" ? 0.5 : 1 }}>
                    <StepGlyph kind={node.kind} podId={ctx.podId} agent={node.kind === "AGENT" ? String(node.config.agent_name ?? "") : null} />
                </View>
                <Text size={15} numberOfLines={1} tone={state === "skipped" || state === "pending" ? color.ink3 : color.ink} style={{ flex: 1, minWidth: 0 }}>
                    {label}
                    <Text size={13} tone={color.ink3}>{"  "}{who}{pass ? " · pass " + pass : ""}</Text>
                </Text>
                {took ? <Num size={12.5} tone={color.ink3}>{took}</Num> : null}
                <StateMark state={state} />
            </Press>
            {open && trace ? <Body node={node} trace={trace} ctx={ctx} /> : null}
        </View>
    );
}

function Body({ node, trace, ctx }: { node: GraphNode; trace: StepRow; ctx: Ctx }) {
    const router = useRouter();
    const wait = ctx.run.wait;
    const waitingHere = Boolean(wait && wait.nodeId === node.id && stillGoing(ctx.run.status));
    const conversation = trace.externalRef ?? (waitingHere ? wait?.externalRef ?? null : null);
    const lead = leadOf(trace.output);
    return (
        <View style={{ gap: 10, paddingLeft: 38, paddingRight: 4, paddingBottom: 12 }}>
            {trace.error ? <Text size={14} tone={color.amber} selectable style={{ lineHeight: 19.5 }}>{trace.error}</Text> : null}
            {node.kind === "AGENT" ? (
                <>
                    {lead ? <Text size={14.5} selectable style={{ lineHeight: 20.5 }}>{lead}</Text> : null}
                    {conversation ? (
                        <View style={{ flexDirection: "row" }}>
                            <Button title={`Open the chat with ${whoOf(node, ctx)}`} kind="outline" height={36} size={14} onPress={() => router.push(routes.chat(ctx.podId, conversation) as never)} />
                        </View>
                    ) : null}
                    <Fields value={trace.output} lead={lead} />
                </>
            ) : node.kind === "FORM" ? (
                waitingHere && wait?.type === "HUMAN" && wait.schema ? <Note tone={color.ink3} style={{ paddingHorizontal: 0 }}>Answered above.</Note> : <Fields value={trace.output} />
            ) : node.kind === "DECISION" ? (
                <Text size={14.5} style={{ lineHeight: 20.5 }}>{sayDecided(trace.output, (id) => ctx.graph.nodes.get(id)?.label || id)}</Text>
            ) : (
                <Fields value={trace.output} lead={lead} showLead />
            )}
        </View>
    );
}

/** A history row when the workflow's graph could not be read. */
function FlatRow({ podId, step }: { podId: string; step: StepRow }) {
    const router = useRouter();
    const state = stateOfTrace(step.status);
    const lead = leadOf(step.output);
    return (
        <View style={{ borderBottomWidth: 1, borderBottomColor: color.line, paddingBottom: 8 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 44, paddingHorizontal: 4 }}>
                <View style={{ width: 22, alignItems: "center" }}><Icon name="automation" size={15} tone={color.ink2} /></View>
                <Text size={15} numberOfLines={1} style={{ flex: 1 }}>{step.nodeId || "Unnamed step"}</Text>
                <StateMark state={state} />
            </View>
            <View style={{ paddingLeft: 38, gap: 8 }}>
                {step.error ? <Text size={14} tone={color.amber}>{step.error}</Text> : null}
                <Fields value={step.output} lead={lead} showLead />
                {step.externalRef ? <View style={{ flexDirection: "row" }}><Button title="Open the chat" kind="outline" height={36} size={14} onPress={() => router.push(routes.chat(podId, step.externalRef!) as never)} /></View> : null}
            </View>
        </View>
    );
}

function Context({ value }: { value: Record<string, unknown> }) {
    const [open, setOpen] = useState(false);
    return (
        <View style={{ paddingTop: 14 }}>
            <Press label={open ? "Hide the run context" : "Show the run context"} onPress={() => setOpen((was) => !was)} style={{ minHeight: 40, justifyContent: "center", paddingHorizontal: 4 }}>
                <Text size={14} tone={color.ink2}>{open ? "Hide the run context" : "Run context"}</Text>
            </Press>
            {open ? <Data text={JSON.stringify(value, null, 2)} /> : null}
        </View>
    );
}
