import { useMemo, useState } from "react";
import * as WebBrowser from "expo-web-browser";
import { useSession } from "../../session/session";
import { connectionsPath, connectorLabel, connectorsFor } from "@web/data/pod-connectors";
import { View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useLemma } from "../../data/hooks";
import { NEW_CHAT, routes } from "../../data/routes";
import { Loading } from "../../ui/chrome";
import { Icon } from "../../ui/icons";
import { Button } from "../../ui/press";
import { Num, Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { AskSheet, Quiet } from "../tables/bits";
import { usePodConnectors, useMembers, useMyId, useRunsInFlight, useSchedules, useScheduleWrites, useStartRun, useWaitingOnMe, useWorkflow, useWorkflowRuns, useWorkflows } from "./hooks";
import { healthMark, markOf, personOf, runsFor, sayAge, sayRunLine, stampAt, stepCount, stepNames, whatStarts } from "./model";
import { Outline } from "./outline";
import { Choices, Heading, Line, Note, RunEnd, Tags, Trouble } from "./parts";
import { workRoutes } from "./routes";
import { Screen, Title } from "./screen";
import { boardOf, sayHeldBy, type BoardCard, type BoardColumn } from "@web/workflow/board";
import { CADENCES, healthOf, humanizeName, SCOPE_LABEL, type StandingJob } from "@web/schedule/schedules";
import { schedulesFor, turnOnOf, turnOnRequest, type Automation, type TurnOn } from "@web/workflow/turn-on";

/** One workflow (`space/workflow-page.tsx`): what it is and who it runs for,
 *  Run now, where every run still going is, whether it is on (for you, or
 *  for the Lemma) and how to turn it on, its steps, and every run it made. */
export function WorkflowScreen() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string; name: string; back?: string }>();
    const podId = String(params.podId);
    const name = String(params.name);
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "the Lemma";
    const flows = useWorkflows(podId);
    const flow = flows.data?.find((one) => one.name === name) ?? null;
    const read = useWorkflow(podId, name);
    const runs = useWorkflowRuns(podId, name);
    const waiting = useWaitingOnMe(podId);
    const mine = useMemo(() => new Set((waiting.data ?? []).map((one) => one.run.id)), [waiting.data]);
    const start = useStartRun(podId);
    const [problem, setProblem] = useState<string | null>(null);
    const [asking, setAsking] = useState(false);
    const steps = stepCount(read.shape, flow?.steps ?? null);
    const perPerson = flow?.perPerson ?? read.perPerson;
    const names = useMemo(() => stepNames(read.shape), [read.shape]);

    const runNow = () => {
        setProblem(null);
        start.mutate(name, {
            onSuccess: (made) => { if (made) router.push(workRoutes.run(podId, made.id) as never); },
            onError: (error) => setProblem((error as { statusCode?: number }).statusCode === 403 ? "You may not run this workflow." : error instanceof Error ? error.message : "Couldn’t start a run."),
        });
    };
    const refresh = () => Promise.all([flows.refetch(), read.query.refetch(), runs.refetch()]);
    const all = runs.data?.pages.flatMap((page) => page.items) ?? [];

    return (
        <Screen podId={podId} back={params.back ? String(params.back) : lemmaName} onRefresh={refresh}>
            <Title glyph={<Icon name="automation" size={24} tone={color.ink2} />} title={humanizeName(name)}>
                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{flow?.description || read.description || "No description written."}</Text>
                <Tags tags={[
                    { text: read.automation.kind === "rows" ? "Each row’s owner" : perPerson ? "Each person" : "Admin" },
                    ...(flow && !flow.active ? [{ text: "Paused" }] : []),
                    ...(steps !== null ? [{ text: steps + (steps === 1 ? " step" : " steps") }] : []),
                ]} />
                <View style={{ flexDirection: "row", gap: 8, paddingTop: 4 }}>
                    <Button title={start.isPending ? "Starting…" : "Run now"} busy={start.isPending} disabled={flow ? !flow.active : false} onPress={runNow} />
                    <Button title="Ask to change it" kind="outline" onPress={() => setAsking(true)} />
                </View>
            </Title>
            {problem ? <Trouble text={problem} /> : null}

            <Board podId={podId} workflowId={flow?.id ?? null} shape={read.shape} />

            <HowItStarts podId={podId} name={name} lemmaName={lemmaName} automation={read.automation} perPerson={perPerson} ready={!read.query.isPending} />

            <Heading>How it runs</Heading>
            {read.query.isPending ? <Loading label="Reading its steps" /> : read.query.isError ? (
                <Trouble text={(read.query.error as { statusCode?: number }).statusCode === 403 ? "You may not read this workflow’s steps." : "Couldn’t read the steps."} onRetry={() => void read.query.refetch()} />
            ) : !read.shape ? <Note>This workflow came back in a shape this app could not read.</Note> : <Outline shape={read.shape} />}

            <Heading count={runs.isSuccess ? all.length : null}>Runs</Heading>
            {runs.isPending ? <Loading label="Reading its runs" /> : runs.isError ? (
                <Trouble text={(runs.error as { statusCode?: number }).statusCode === 403 ? "You may not read these runs." : "Couldn’t read the runs."} onRetry={() => void runs.refetch()} />
            ) : all.length === 0 ? <Note>It has never run.</Note> : (
                all.map((run) => (
                    <Line
                        key={run.id}
                        title={run.title ?? sayStatusWord(run.status)}
                        quiet={sayRunLine(run, names)}
                        when={stampAt(run.startedAt ?? run.createdAt)}
                        end={<RunEnd mark={markOf(run, mine.has(run.id))} />}
                        onPress={() => router.push(workRoutes.run(podId, run.id) as never)}
                    />
                ))
            )}
            {runs.hasNextPage ? <Quiet text={runs.isFetchingNextPage ? "Loading…" : "More runs"} tone={color.ink} onPress={() => { if (!runs.isFetchingNextPage) void runs.fetchNextPage(); }} /> : null}

            <AskSheet open={asking} onClose={() => setAsking(false)} name={lemmaName} start={`Change the ${humanizeName(name).toLowerCase()} workflow: `} onSend={(text) => router.push(routes.chat(podId, NEW_CHAT, text) as never)} />
        </Screen>
    );
}

function sayStatusWord(status: string): string {
    return { PENDING: "Starting", RUNNING: "Running", WAITING: "Waiting on a person", COMPLETED: "Completed", FAILED: "Failed", CANCELLED: "Cancelled" }[status] ?? "Unknown";
}

/* ── in progress ─────────────────────────────────────────────────────── */

const HOW: Record<string, string> = { SCHEDULED: "On a schedule", EVENT: "From an event", DATASTORE_EVENT: "From a row change" };

/** Where every run still going is, a pile per step (`space/run-board.tsx`).
 *  The web's columns stack on a phone; an empty step is one quiet line.
 *  Absent when nothing is going: an empty board is a picture of the steps,
 *  and "How it runs" draws those. */
function Board({ podId, workflowId, shape }: { podId: string; workflowId: string | null; shape: ReturnType<typeof useWorkflow>["shape"] }) {
    const router = useRouter();
    const runs = useRunsInFlight(podId, workflowId);
    const waits = useWaitingOnMe(podId);
    const members = useMembers(podId);
    const me = useMyId();
    const myMember = members.data?.find((member) => member.userId && member.userId === me)?.id ?? null;
    const board = useMemo(
        () => boardOf(shape, runs.data ?? [], workflowId ?? "", new Map((waits.data ?? []).map((one) => [one.run.id, one.wait])), myMember),
        [shape, runs.data, workflowId, waits.data, myMember],
    );
    if (runs.isError) return <Trouble text="Couldn’t read the runs still going." onRetry={() => void runs.refetch()} />;
    if (!runs.isSuccess || !shape || !workflowId || board.count === 0) return null;

    /* "Started by", never "From": a card that says "From you" beside one
       that says "Waiting on you" reads as one more thing to do. */
    const starter = (card: BoardCard) => {
        if (card.run.startType && HOW[card.run.startType]) return HOW[card.run.startType];
        return "Started by " + (personOf(card.run.userId, me, members.data ?? []) ?? "someone here");
    };
    const heldBy = (card: BoardCard, column: BoardColumn) => {
        if (card.yours) return "Waiting on you";
        const member = card.run.waitingOn?.assigneeId ? members.data?.find((one) => one.id === card.run.waitingOn?.assigneeId) : null;
        if (member) return member.userId && member.userId === me ? "Waiting on you" : "Waiting on " + member.name;
        return sayHeldBy(column.step, card.run);
    };
    return (
        <View>
            <Heading count={board.count} right={board.mine > 0 ? <Text size={12.5} tone={color.violetInk}>{board.mine} waiting on you</Text> : null}>In progress</Heading>
            {board.columns.map((column) => {
                const title = column.step ? column.step.label || humanizeName(column.step.id) || "An unnamed step" : "Between steps";
                return (
                    <View key={column.id || "elsewhere"} style={{ paddingTop: 6 }}>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 4, minHeight: 28 }}>
                            <Text size={14} tone={column.cards.length ? color.ink : color.ink3} numberOfLines={1} style={{ flex: 1 }}>{title}</Text>
                            <Num size={13} tone={color.ink3}>{column.cards.length}</Num>
                        </View>
                        {column.cards.map((card) => (
                            <Line
                                key={card.run.id}
                                title={card.run.title ?? starter(card)}
                                quiet={heldBy(card, column) + " · " + sayAge(card)}
                                tone={card.yours ? color.violetInk : color.ink}
                                end={<RunEnd mark={markOf(card.run, card.yours)} />}
                                onPress={() => router.push(workRoutes.run(podId, card.run.id) as never)}
                            />
                        ))}
                    </View>
                );
            })}
        </View>
    );
}

/* ── how it starts ───────────────────────────────────────────────────── */

function HowItStarts({ podId, name, lemmaName, automation, perPerson, ready }: { podId: string; name: string; lemmaName: string; automation: Automation; perPerson: boolean; ready: boolean }) {
    const router = useRouter();
    const me = useMyId();
    const members = useMembers(podId);
    const schedules = useSchedules(podId, lemmaName);
    const jobs = useMemo(() => schedulesFor(schedules.data ?? [], name), [schedules.data, name]);
    const state = turnOnOf(automation, perPerson, jobs, me);
    const owner = (job: StandingJob) => personOf(job.ownerId, me, members.data ?? []) ?? "someone here";
    if (!ready) return null;
    return (
        <View>
            <Heading>How it starts</Heading>
            <View style={{ gap: 4, paddingHorizontal: 4 }}>
                <Text size={15} style={{ lineHeight: 21 }}>{whatStarts(automation)}</Text>
                <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19 }}>{runsFor(automation, perPerson)}</Text>
                <StateLine state={state} owner={owner} />
            </View>
            {state.status === "off" || (state.status === "stopped" && !state.byYou && perPerson) ? (
                <TurnOn podId={podId} name={name} automation={automation} perPerson={perPerson} />
            ) : null}
            {jobs.map((job) => {
                const health = healthOf(job);
                const mine = owner(job) === "you";
                return (
                    <Line
                        key={job.id}
                        glyph={<Icon name="clock" size={16} tone={color.ink2} />}
                        title={job.trigger}
                        quiet={[SCOPE_LABEL[job.scope], mine ? "yours" : "by " + owner(job), job.lastFiredAt ? "fired " + (stampAt(job.lastFiredAt) ?? "") : null, health.tone !== "ok" ? health.line : null].filter(Boolean).join(" · ")}
                        end={healthMark(health.tone) === "bad" ? <RunEnd mark="failed" /> : null}
                        onPress={() => router.push(workRoutes.schedule(podId, job.id) as never)}
                    />
                );
            })}
            {perPerson && automation.kind !== "manual" && automation.kind !== "rows" ? (
                <Note tone={color.ink3} style={{ fontSize: 13 }}>Other people’s own switches are private to them, so they are not listed here.</Note>
            ) : null}
        </View>
    );
}

function StateLine({ state, owner }: { state: TurnOn; owner: (job: StandingJob) => string }) {
    if (state.status === "manual") return null;
    const dot = (tone: string) => <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: tone, marginTop: 7 }} />;
    if (state.status === "on") {
        return <View style={{ flexDirection: "row", gap: 8, paddingTop: 4 }}>{dot(color.violet)}<Text size={14.5} style={{ flex: 1 }}>On{state.byYou ? ": you turned it on." : ": turned on by " + owner(state.job) + "."}</Text></View>;
    }
    if (state.status === "stopped") {
        const health = healthOf(state.job);
        return <View style={{ flexDirection: "row", gap: 8, paddingTop: 4 }}>{dot(color.amber)}<Text size={14.5} tone={color.amber} style={{ flex: 1, lineHeight: 20 }}>{state.job.needsSetup || health.line || "Paused"}{state.byYou ? "" : " (set up by " + owner(state.job) + ")"}</Text></View>;
    }
    return <View style={{ flexDirection: "row", gap: 8, paddingTop: 4 }}>{dot(color.line2)}<Text size={14.5} tone={color.ink2} style={{ flex: 1 }}>{state.forYou ? "Not on for you yet." : "Off: nobody has turned it on."}</Text></View>;
}

/** Turning it on is making a schedule for it: yours (each person) or the
 *  Lemma's (admin), on the times you pick, or through one of the Lemma's
 *  connectors where it listens to an app. Whose account that connector
 *  listens as is its own decision: yours, for one each member connects,
 *  which the server asks you to connect if you have not. */
function TurnOn({ podId, name, automation, perPerson }: { podId: string; name: string; automation: Automation; perPerson: boolean }) {
    const writes = useScheduleWrites(podId);
    const [cron, setCron] = useState(CADENCES[0].cron);
    const [picked, setPicked] = useState("");
    const connectors = usePodConnectors(podId, automation.kind === "event");
    const offered = connectorsFor(connectors.data ?? [], automation.kind === "event" ? automation.connectorId : "");
    const usable = offered.filter((one) => one.status === "READY");
    const chosen = picked || usable[0]?.slug || "";
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const request = turnOnRequest(name, automation, perPerson, { cron, timezone: zone, podConnector: chosen });
    const { server } = useSession();
    const verb = perPerson && automation.kind !== "rows" ? "Turn on for me" : "Turn on for everyone";
    const connector = automation.kind === "event" ? automation.connectorId : "";
    return (
        <View style={{ gap: 10, paddingHorizontal: 4, paddingTop: 10, paddingBottom: 4 }}>
            {automation.kind === "time" ? (
                <Choices label="When" value={cron} options={CADENCES.map((one) => ({ value: one.cron, label: one.label }))} onPick={setCron} />
            ) : null}
            {automation.kind === "event" ? (
                connectors.isPending ? <Note>Looking for this Lemma’s {connector || "app"} connector…</Note>
                    : connectors.isError ? <Trouble text="This Lemma’s connectors couldn’t be read." onRetry={() => void connectors.refetch()} />
                    : usable.length === 0 ? (
                        <View style={{ gap: 4 }}>
                            <Note>{offered.length === 0
                                ? "It listens through a " + (connector || "connected app") + " connector, and this Lemma has none yet."
                                : "This Lemma’s " + (connector || "app") + " connector can’t be used yet: " + offered.map((one) => connectorLabel(one)).join("; ") + "."}</Note>
                            {/* Connections are set up on the web for now. */}
                            <Quiet text="Set up connections" tone={color.ink} onPress={() => void WebBrowser.openBrowserAsync(server.web + connectionsPath(podId, offered[0]?.slug))} />
                        </View>
                    ) : usable.length > 1 ? (
                        <Choices label="Through" value={chosen} options={usable.map((one) => ({ value: one.slug, label: connectorLabel(one) }))} onPick={setPicked} />
                    ) : <Note>Listens through {connectorLabel(usable[0])}.</Note>
            ) : null}
            <View style={{ flexDirection: "row" }}>
                <Button title={writes.create.isPending ? "Turning on…" : verb} busy={writes.create.isPending} disabled={!request} onPress={() => { if (request) writes.create.mutate(request); }} />
            </View>
            {writes.create.isError ? <Trouble text={writes.create.error instanceof Error ? writes.create.error.message : "Couldn’t turn it on."} /> : null}
        </View>
    );
}

