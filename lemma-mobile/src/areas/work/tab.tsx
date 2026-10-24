import { useMemo, useState } from "react";
import { ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { NEW_CHAT, routes } from "../../data/routes";
import { useLemma } from "../../data/hooks";
import { Glyph } from "../../thread/glyphs";
import { Loading } from "../../ui/chrome";
import { LemmaFace } from "../../ui/face";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Num, Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { AskSheet, Quiet } from "../tables/bits";
import { RECENT_RUNS, useAgents, useMyId, useRecentRuns, useSchedules, useStepNames, useWaitingOnMe, useWorkflows } from "./hooks";
import { flowLine, flowSummaries, healthMark, markOf, runsLine, sayRunLine, scheduleGroups, splitRuns, stampAt } from "./model";
import { Heading, Line, Note, RunEnd, Tag, Trouble } from "./parts";
import { PlanView } from "./plan";
import { NEW_SCHEDULE, RUNS_VIEWS, workRoutes, type RunsView } from "./routes";
import { FormSheet } from "./wait-form";
import { Xray } from "./xray";
import { healthOf, humanizeName, SCOPE_LABEL, type StandingJob } from "@web/schedule/schedules";
import type { Assignment, RunRow, WorkflowRow } from "@web/workflow/runs";

const VIEW_LABEL: Record<RunsView, string> = {
    workflows: "Workflows",
    waiting: "Waiting on you",
    running: "Running",
    history: "History",
    schedules: "Schedules",
    agents: "Agents",
    xray: "X-ray",
};

/** What a Lemma runs, and how each run went, on its page (PARITY.md, D).
 *
 *  The web's Workflows page (workflows, waiting on you, running, recent
 *  runs), its Standing work and its Agents, and the `/o` frame's X-ray, as
 *  one tab with a chip for each: on a phone the views are read one at a
 *  time, and a chip row is how this app narrows a list (Needs you). Every
 *  row opens its own screen; "waiting on you" is answered where it stands. */
export function RunsTab({ podId, name }: { podId: string; name: string }) {
    const params = useLocalSearchParams<{ view?: string }>();
    const asked = RUNS_VIEWS.find((one) => one === params.view) ?? "workflows";
    const [view, setView] = useState<RunsView>(asked);
    const flows = useWorkflows(podId);
    const runs = useRecentRuns(podId);
    const waiting = useWaitingOnMe(podId);
    const schedules = useSchedules(podId, name);
    const agents = useAgents(podId, name);
    const split = useMemo(() => splitRuns(runs.data ?? [], waiting.data ?? []), [runs.data, waiting.data]);

    const counts: Record<RunsView, number | null> = {
        workflows: flows.data?.length ?? null,
        waiting: waiting.data?.length ?? null,
        running: runs.isSuccess ? split.running.length : null,
        history: null,
        schedules: schedules.data?.length ?? null,
        agents: agents.data ? agents.data.filter((one) => !one.front).length : null,
        xray: null,
    };

    return (
        <View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -16 }} contentContainerStyle={{ gap: 6, paddingHorizontal: 16, paddingBottom: 10 }}>
                {RUNS_VIEWS.map((one) => {
                    const on = one === view;
                    const count = counts[one];
                    const hot = one === "waiting" && (count ?? 0) > 0;
                    return (
                        <Press
                            key={one}
                            role="tab"
                            label={`${VIEW_LABEL[one]}${count ? `, ${count}` : ""}`}
                            accessibilityState={{ selected: on }}
                            onPress={() => setView(one)}
                            hitSlop={{ top: 6, bottom: 6 }}
                            style={{ height: 32, paddingHorizontal: 12, borderRadius: 999, backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2, flexDirection: "row", alignItems: "center", gap: 5 }}
                        >
                            <Text size={13.5} tone={on ? color.onInk : color.ink}>{VIEW_LABEL[one]}</Text>
                            {count ? <Num size={13.5} tone={on ? color.onInk : hot ? color.violet : color.ink3}>{count}</Num> : null}
                        </Press>
                    );
                })}
            </ScrollView>
            {view === "workflows" ? <Workflows podId={podId} name={name} flows={flows} runs={runs.data ?? []} waiting={waiting.data ?? []} schedules={schedules.data ?? []} agentNames={agents.data?.map((one) => one.name) ?? null} />
                : view === "waiting" ? <Waiting podId={podId} query={waiting} flows={flows.data ?? []} />
                : view === "running" || view === "history" ? <RunList podId={podId} view={view} query={runs} rows={view === "running" ? split.running : split.history} flows={flows.data ?? []} waiting={waiting.data ?? []} />
                : view === "schedules" ? <Schedules podId={podId} name={name} query={schedules} />
                : view === "agents" ? <Agents podId={podId} name={name} query={agents} />
                : <Xray podId={podId} name={name} />}
        </View>
    );
}

/** A Lemma's plan (`/plan.md`), on its page. */
export function PlanTab({ podId, name }: { podId: string; name: string }) {
    const lemma = useLemma(podId);
    return <PlanView podId={podId} name={name} description={lemma?.description ?? null} />;
}

/* ── workflows ───────────────────────────────────────────────────────── */

function Workflows({ podId, name, flows, runs, waiting, schedules, agentNames }: {
    podId: string;
    name: string;
    flows: ReturnType<typeof useWorkflows>;
    runs: RunRow[];
    waiting: Assignment[];
    schedules: StandingJob[];
    agentNames: string[] | null;
}) {
    const router = useRouter();
    const [asking, setAsking] = useState<string | null>(null);
    const now = Date.now();
    const rows = useMemo(() => flowSummaries({ flows: flows.data ?? [], runs, waiting, schedules, agentNames }), [flows.data, runs, waiting, schedules, agentNames]);
    const ask = <AskSheet open={asking !== null} onClose={() => setAsking(null)} name={name} start={asking ?? ""} onSend={(text) => router.push(routes.chat(podId, NEW_CHAT, text) as never)} />;

    if (flows.isPending) return <Loading label="Reading its workflows" />;
    if (flows.isError) return <Trouble text={forbidden(flows.error) ? "You may not list the workflows here." : "Couldn’t read the workflows."} onRetry={() => void flows.refetch()} />;
    if (rows.length === 0) {
        return (
            <View style={{ paddingHorizontal: 4, paddingVertical: 8, gap: 10 }}>
                <Text size={17} weight="medium">No workflows yet</Text>
                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>Steps that run in order, wait for people where they have to, and pick up again. {name} sets them up.</Text>
                <View style={{ flexDirection: "row" }}><Button title={`Ask ${name} for one`} onPress={() => setAsking("Set up a workflow that ")} /></View>
                {ask}
            </View>
        );
    }
    return (
        <View>
            <Note style={{ paddingTop: 0 }}>Steps that run in order, wait for people where they have to, and pick up again.</Note>
            {rows.map((row) => (
                <Line
                    key={row.flow.id}
                    glyph={<Icon name="automation" size={17} tone={color.ink2} />}
                    title={humanizeName(row.flow.name)}
                    quiet={flowLine(row, runs.length < RECENT_RUNS, now)}
                    label={`${humanizeName(row.flow.name)}${row.waiting ? `, ${row.waiting} waiting on you` : row.going ? ", running" : ""}`}
                    end={<RunEnd mark={row.waiting ? "yours" : row.going ? "going" : row.last && row.last.status === "FAILED" ? "failed" : "quiet"} />}
                    onPress={() => router.push(workRoutes.workflow(podId, row.flow.name) as never)}
                />
            ))}
            <View style={{ paddingTop: 6 }}>
                <Quiet text="New workflow" label={`Ask ${name} to set up a workflow`} onPress={() => setAsking("Set up a workflow that ")} />
            </View>
            {ask}
        </View>
    );
}

/* ── waiting on you ──────────────────────────────────────────────────── */

/** Forms waiting on the reader, longest first, each answered in a sheet
 *  where it stands. */
function Waiting({ podId, query, flows }: { podId: string; query: ReturnType<typeof useWaitingOnMe>; flows: WorkflowRow[] }) {
    const [open, setOpen] = useState<Assignment | null>(null);
    const [sent, setSent] = useState<Set<string>>(new Set());
    const names = useStepNames(podId, (query.data ?? []).map((one) => nameOf(flows, one.run.workflowId)).filter((one): one is string => Boolean(one)));
    const now = Date.now();
    if (query.isPending) return <Loading label="Checking what waits on you" />;
    if (query.isError) return <Trouble text="Couldn’t check what waits on you." onRetry={() => void query.refetch()} />;
    const rows = [...query.data].filter((one) => !sent.has(one.wait.id)).sort((a, b) => (Date.parse(a.wait.createdAt ?? "") || 0) - (Date.parse(b.wait.createdAt ?? "") || 0));
    if (rows.length === 0) return <Note>{sent.size ? "Sent. Nothing else waits on you here." : "Nothing is waiting on you."}</Note>;
    return (
        <View>
            {rows.map((row) => {
                const flow = nameOf(flows, row.run.workflowId);
                const step = (flow && names.get(flow)?.get(row.wait.nodeId)) || row.wait.nodeId;
                return (
                    <Line
                        key={row.wait.id}
                        glyph={<Icon name="people" size={17} tone={color.ink2} />}
                        title={row.run.title ?? (flow ? humanizeName(flow) : "A workflow")}
                        quiet={(row.run.title && flow ? humanizeName(flow) + " · " : "") + step}
                        when={stampAt(row.wait.createdAt, now)}
                        label={`${flow ? humanizeName(flow) : "A workflow"}: ${step}, waiting on you. Answer`}
                        end={<RunEnd mark="yours" />}
                        onPress={() => setOpen(row)}
                    />
                );
            })}
            {open ? (
                <FormSheet
                    podId={podId}
                    runId={open.run.id}
                    title={(() => { const flow = nameOf(flows, open.run.workflowId); return (flow && names.get(flow)?.get(open.wait.nodeId)) || "Fill in the form"; })()}
                    workflow={nameOf(flows, open.run.workflowId)}
                    open
                    onClose={() => setOpen(null)}
                    onSent={() => { setSent((was) => new Set(was).add(open.wait.id)); setOpen(null); }}
                />
            ) : null}
        </View>
    );
}

function nameOf(flows: readonly WorkflowRow[], id: string | null): string | null {
    return flows.find((one) => one.id === id)?.name ?? null;
}

/* ── running, history ────────────────────────────────────────────────── */

function RunList({ podId, view, query, rows, flows, waiting }: {
    podId: string;
    view: "running" | "history";
    query: ReturnType<typeof useRecentRuns>;
    rows: RunRow[];
    flows: WorkflowRow[];
    waiting: Assignment[];
}) {
    const router = useRouter();
    const mine = new Set(waiting.map((one) => one.run.id));
    const names = useStepNames(podId, rows.slice(0, 40).map((run) => nameOf(flows, run.workflowId)).filter((one): one is string => Boolean(one)));
    const now = Date.now();
    if (query.isPending) return <Loading label="Reading its runs" />;
    if (query.isError) return <Trouble text={forbidden(query.error) ? "You may not read runs here." : "Couldn’t read the runs."} onRetry={() => void query.refetch()} />;
    if (rows.length === 0) return <Note>{view === "running" ? "Nothing is running." : "Nothing has run yet."}</Note>;
    return (
        <View>
            {rows.map((run) => {
                const flow = nameOf(flows, run.workflowId);
                const label = flow ? humanizeName(flow) : "A workflow";
                return (
                    <Line
                        key={run.id}
                        title={run.title ?? label}
                        quiet={(run.title ? label + " · " : "") + sayRunLine(run, flow ? names.get(flow) : undefined)}
                        when={stampAt(run.startedAt ?? run.createdAt, now)}
                        end={<RunEnd mark={markOf(run, mine.has(run.id))} />}
                        onPress={() => router.push(workRoutes.run(podId, run.id) as never)}
                    />
                );
            })}
            {view === "history" && (query.data?.length ?? 0) >= RECENT_RUNS ? <Note tone={color.ink3}>The latest {RECENT_RUNS} runs. Every run of a workflow is on its page.</Note> : null}
        </View>
    );
}

/* ── schedules ───────────────────────────────────────────────────────── */

/** Read as it draws: `color` holds whichever palette is in use. */
const toneMark = (tone: "ok" | "off" | "warn" | "bad") => (healthMark(tone) === "bad" ? color.amber : null);

function Schedules({ podId, name, query }: { podId: string; name: string; query: ReturnType<typeof useSchedules> }) {
    const router = useRouter();
    const me = useMyId();
    const now = Date.now();
    if (query.isPending) return <Loading label="Reading the schedules" />;
    if (query.isError) return <Trouble text={forbidden(query.error) ? "You may not read these schedules." : "Couldn’t read the schedules."} onRetry={() => void query.refetch()} />;
    const groups = scheduleGroups(query.data, me);
    const shared = groups.some((group) => group.key === "space");
    return (
        <View>
            {query.data.length === 0 ? <Note>No schedules yet. Set up recurring work when {name} is ready.</Note> : null}
            {query.data.length > 0 && me === null ? <Loading /> : null}
            {groups.map((group, index) => (
                <View key={group.key}>
                    {shared ? <Heading first={index === 0}>{group.title}</Heading> : null}
                    {group.rows.map((job) => {
                        const health = healthOf(job);
                        const dot = toneMark(health.tone);
                        return (
                            <Line
                                key={job.id || job.name}
                                glyph={<Icon name="clock" size={17} tone={job.active ? color.ink2 : color.ink3} />}
                                title={job.title}
                                tone={job.active ? color.ink : color.ink2}
                                quiet={[SCOPE_LABEL[job.scope], !job.active ? "paused" : null, job.trigger].filter(Boolean).join(" · ")}
                                when={job.lastFiredAt ? stampAt(job.lastFiredAt, now) : null}
                                label={`${job.title}. ${runsLine(job, name)}. ${health.line || job.trigger}`}
                                end={dot ? <View accessibilityLabel={health.line} style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: dot }} /> : null}
                                onPress={job.broken ? undefined : () => router.push(workRoutes.schedule(podId, job.id) as never)}
                            />
                        );
                    })}
                </View>
            ))}
            {query.data.length > 0 ? <Note tone={color.ink3} style={{ fontSize: 13, paddingTop: 10 }}>Personal schedules are private to whoever made them. Share one with everyone here and they can set up their own copy; it still runs as you.</Note> : null}
            <View style={{ paddingTop: 2 }}>
                <Quiet text="New schedule" onPress={() => router.push(workRoutes.schedule(podId, NEW_SCHEDULE) as never)} />
            </View>
        </View>
    );
}

/* ── agents ──────────────────────────────────────────────────────────── */

/** The agents behind the Lemma, the Lemma's own left out: this list is on
 *  its page already, and what belongs here is what it hands work to. */
function Agents({ podId, name, query }: { podId: string; name: string; query: ReturnType<typeof useAgents> }) {
    const router = useRouter();
    if (query.isPending) return <Loading label="Reading its agents" />;
    if (query.isError) return <Trouble text={forbidden(query.error) ? "You may not list these agents." : "Couldn’t read the agents."} onRetry={() => void query.refetch()} />;
    const rows = query.data.filter((one) => !one.front);
    const front = query.data.find((one) => one.front);
    return (
        <View>
            {rows.length === 0 ? <Note>{name} does its own work, and hands nothing on.</Note> : <Note style={{ paddingTop: 0 }}>Who {name} hands work to. Each one is changed by talking to it, or here.</Note>}
            {rows.map((row, index) => (
                <Line
                    key={row.name || "unnamed-" + index}
                    glyph={row.broken ? <Glyph name="agent" size={16} tone={color.ink3} /> : <LemmaFace id={podId + ":" + row.name} icon={row.iconUrl} size={22} />}
                    title={row.label}
                    quiet={[row.takesInput ? "called by workflows" : null, row.visibility === "RESTRICTED" ? "restricted" : null, row.blurb].filter(Boolean).join(" · ")}
                    onPress={row.broken ? undefined : () => router.push(workRoutes.agent(podId, row.name) as never)}
                />
            ))}
            {front ? (
                <View style={{ paddingTop: 6, flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 4 }}>
                    <Tag text="Answers here" />
                    <Quiet text={`${name}’s own agent`} onPress={() => router.push(workRoutes.agent(podId, front.name) as never)} />
                </View>
            ) : null}
        </View>
    );
}

function forbidden(error: unknown): boolean {
    return (error as { statusCode?: number } | null)?.statusCode === 403;
}
