import { useMemo, useState } from "react";
import { View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useLemma } from "../../data/hooks";
import { routes } from "../../data/routes";
import { isPodDefaultAgent } from "@web/data/agent-names";
import { Loading } from "../../ui/chrome";
import { Form } from "../../ui/form/form";
import type { FormField } from "../../ui/form/model";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color, font } from "../../ui/tokens";
import { useTableList } from "../tables/hooks";
import { Problem, Quiet } from "../tables/bits";
import { useAgents, useMembers, useMyId, useRetryFiring, useScheduleRuns, useSchedules, useScheduleWrites, useServerEvents, useWorkflows } from "./hooks";
import { healthMark, personOf, runsLine, SCOPE_NOTE, stampAt } from "./model";
import { Heading, Line, Note, Tags, Trouble } from "./parts";
import { NEW_SCHEDULE, workRoutes } from "./routes";
import { Screen, Title } from "./screen";
import {
    blankDraft, canRetry, CADENCES, copyNeedOf, copyRequest, createRequest, draftProblems, healthOf, humanizeName, may, SCHEDULE_EDIT, SCOPE_LABEL, serverGroups, WHENS,
    type ScheduleDraft, type ScheduleRun, type StandingJob,
} from "@web/schedule/schedules";

/** `schedule/[id]`: one schedule, or the form that makes one. */
export function ScheduleScreen() {
    const params = useLocalSearchParams<{ podId: string; id: string; back?: string }>();
    const podId = String(params.podId);
    const id = String(params.id);
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "the Lemma";
    const back = params.back ? String(params.back) : name;
    return id === NEW_SCHEDULE ? <NewSchedule podId={podId} name={name} back={back} /> : <OneSchedule podId={podId} id={id} name={name} back={back} />;
}

/* ── one schedule ────────────────────────────────────────────────────── */

/** Read as it draws: `color` holds whichever palette is in use. */
const toneInk = (tone: "ok" | "off" | "warn" | "bad") => (healthMark(tone) === "bad" ? color.amber : color.ink2);

/** What fires it, what it runs, when it last fired and whether that worked,
 *  the failure streak; then pause and resume, who sees it, a copy of your
 *  own, and every firing with a retry on the ones that failed
 *  (`schedule/standing-work.tsx`). */
function OneSchedule({ podId, id, name, back }: { podId: string; id: string; name: string; back: string }) {
    const router = useRouter();
    const me = useMyId();
    const members = useMembers(podId);
    const schedules = useSchedules(podId, name);
    const writes = useScheduleWrites(podId);
    const job = schedules.data?.find((one) => one.id === id) ?? null;
    const [problem, setProblem] = useState<string | null>(null);

    if (schedules.isPending) return <Screen podId={podId} back={back}><Loading label="Reading the schedule" /></Screen>;
    if (schedules.isError) return <Screen podId={podId} back={back}><Trouble text="Couldn’t read the schedules." onRetry={() => void schedules.refetch()} /></Screen>;
    if (!job) return <Screen podId={podId} back={back}><Note>This schedule is gone, or it is someone else’s and private to them.</Note></Screen>;

    const health = healthOf(job);
    const mine = !job.ownerId || job.ownerId === me;
    const owner = personOf(job.ownerId, me, members.data ?? []);
    const editable = may(job, SCHEDULE_EDIT);
    const target = job.target.kind === "workflow" && job.target.name ? workRoutes.workflow(podId, job.target.name)
        : job.target.kind === "agent" && job.target.name ? workRoutes.agent(podId, isPodDefaultAgent(job.target.name) ? "pod_default" : job.target.name) : null;
    const done = { onError: (error: unknown) => setProblem(error instanceof Error ? error.message : "Couldn’t change this schedule.") };

    return (
        <Screen podId={podId} back={back} onRefresh={() => schedules.refetch()}>
            <Title glyph={<Icon name="clock" size={24} tone={color.ink2} />} title={job.title}>
                <Tags tags={[
                    { text: SCOPE_LABEL[job.scope] },
                    ...(job.needsSetup ? [{ text: "Needs setup", tone: "amber" as const }] : []),
                    ...(!mine ? [{ text: "by " + (owner ?? "someone else") }] : []),
                    ...(mine && job.visibility === "POD" && job.kind !== "DATASTORE" ? [{ text: "Shared" }] : []),
                    ...(job.filter && job.kind !== "TIME" ? [{ text: "Filtered" }] : []),
                    ...(!job.active ? [{ text: "Paused" }] : []),
                ]} />
                <Text size={13} tone={color.ink3}>{SCOPE_NOTE[job.scope]}</Text>
            </Title>

            <View style={{ gap: 6, paddingHorizontal: 4, paddingTop: 8 }}>
                <Text size={15.5} style={{ lineHeight: 21 }}>
                    {job.trigger}
                    {job.triggerLiteral ? <Text size={12.5} tone={color.ink3} style={{ fontFamily: font.num }}>{"  " + job.triggerLiteral}</Text> : null}
                </Text>
                {target ? (
                    <Press role="link" label={runsLine(job, name)} onPress={() => router.push(target as never)} style={{ flexDirection: "row", alignItems: "center", gap: 4, minHeight: 32 }}>
                        <Text size={15} tone={color.ink}>{runsLine(job, name)}</Text>
                        <Icon name="forward" size={13} tone={color.ink3} />
                    </Press>
                ) : <Text size={15} tone={color.ink2}>{runsLine(job, name)}</Text>}
                {job.instruction ? <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20.5 }}>{job.instruction}</Text> : null}
                {job.filter && job.kind !== "TIME" ? <Text size={14} tone={color.ink3} style={{ lineHeight: 19.5 }}>Only when: {job.filter}</Text> : null}
                {health.line ? <Text size={14.5} tone={toneInk(health.tone)} style={{ lineHeight: 20 }}>{health.line}</Text> : null}
                {job.lastError && health.tone === "bad" && job.lastError !== health.line ? <Text size={13.5} tone={color.amber} selectable style={{ lineHeight: 19 }}>{job.lastError}</Text> : null}
                {job.lastFiredAt ? <Text size={13.5} tone={color.ink3}>Fired {stampAt(job.lastFiredAt)}</Text> : !health.line ? <Text size={13.5} tone={color.ink3}>Not triggered yet</Text> : null}
            </View>

            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, paddingTop: 14, paddingHorizontal: 4 }}>
                {mine ? (
                    <>
                        <Button
                            title={writes.active.isPending ? "…" : job.active ? "Pause" : "Resume"}
                            kind={job.active ? "outline" : "primary"}
                            busy={writes.active.isPending}
                            disabled={!editable}
                            onPress={() => writes.active.mutate({ id: job.id, active: !job.active }, done)}
                        />
                        {job.kind !== "DATASTORE" ? (
                            <Button
                                title={job.visibility === "POD" ? "Make private" : "Share with everyone"}
                                kind="outline"
                                busy={writes.visibility.isPending}
                                disabled={!editable}
                                onPress={() => writes.visibility.mutate({ id: job.id, visibility: job.visibility === "POD" ? "PERSONAL" : "POD" }, done)}
                            />
                        ) : null}
                    </>
                ) : <CopyForMe podId={podId} job={job} />}
            </View>
            {mine && !editable ? <Note tone={color.ink3}>You may read this schedule, not change it.</Note> : null}
            {mine && job.kind !== "DATASTORE" && editable ? (
                <Note tone={color.ink3} style={{ fontSize: 13 }}>{job.visibility === "POD" ? "Everyone here can see it and set up their own copy. It still runs as you." : "Only you see it. It runs as you."}</Note>
            ) : null}
            {problem ? <Problem text={problem} onDismiss={() => setProblem(null)} /> : null}

            <Firings podId={podId} job={job} editable={editable} />
        </Screen>
    );
}

/** A copy of someone else's schedule that runs as you. One that listens to
 *  an app names the same connector, which decides whose account it listens
 *  through: yours, for a connector each member connects, which the server
 *  asks you to connect if you have not. */
function CopyForMe({ podId, job }: { podId: string; job: StandingJob }) {
    const writes = useScheduleWrites(podId);
    const need = copyNeedOf(job);
    const [made, setMade] = useState(false);
    const copy = () => {
        const body = copyRequest(job);
        if (body) writes.create.mutate(body, { onSuccess: () => setMade(true) });
    };
    if (need === "not-needed") return <Note>A table change runs as whoever owns the row, so everyone already has it.</Note>;
    if (need === "impossible") return null;
    if (made) return <Note>Set up. Your own copy is under Yours.</Note>;
    return (
        <View style={{ gap: 6 }}>
            <Button title="Set up my own" busy={writes.create.isPending} onPress={copy} />
            <Text size={13} tone={color.ink3}>A copy of your own that runs as you.</Text>
            {writes.create.isError ? <Trouble text={writes.create.error instanceof Error ? writes.create.error.message : "Couldn’t set it up."} /> : null}
        </View>
    );
}

const runDot = (tone: "ok" | "off" | "warn" | "bad") => (tone === "bad" ? color.amber : tone === "warn" ? color.violet : null);

/** Its firings, newest first, each opening the run or the chat it made; the
 *  events its filter skipped are a tap away when the question is "why
 *  didn't it run?". */
function Firings({ podId, job, editable }: { podId: string; job: StandingJob; editable: boolean }) {
    const router = useRouter();
    const [skipped, setSkipped] = useState(false);
    const runs = useScheduleRuns(podId, job.id, skipped);
    const retry = useRetryFiring(podId, job.id);
    const filtered = Boolean(job.filter) && job.kind !== "TIME";
    const open = (run: ScheduleRun) => {
        if (!run.targetRunId) return undefined;
        if (run.targetKind === "workflow") return () => router.push(workRoutes.run(podId, run.targetRunId) as never);
        if (run.targetKind === "agent") return () => router.push(routes.chat(podId, run.targetRunId) as never);
        return undefined;
    };
    return (
        <View>
            <Heading right={filtered ? <Quiet text={skipped ? "Show firings" : "Show skipped events"} onPress={() => setSkipped((was) => !was)} /> : undefined}>{skipped ? "Events the filter skipped" : "Firings"}</Heading>
            {runs.isPending ? <Loading label="Reading its firings" /> : runs.isError ? (
                <Trouble text={(runs.error as { statusCode?: number }).statusCode === 403 ? "You may not read this schedule’s firings." : "Couldn’t read its firings."} onRetry={() => void runs.refetch()} />
            ) : runs.data.length === 0 ? <Note>{skipped ? "Nothing skipped yet." : "No firings yet."}</Note> : runs.data.map((run, at) => {
                const dot = runDot(run.tone);
                const why = run.status === "FILTERED" && !run.error ? run.judgement : run.error;
                const tags = [run.retryOf ? "a retry" : null, run.attempts > 1 ? run.attempts + " attempts" : null].filter(Boolean).join(" · ");
                return (
                    <Line
                        key={run.id || "unreadable-" + at}
                        title={run.outcome}
                        quiet={[tags, why].filter(Boolean).join(" · ")}
                        when={stampAt(run.at)}
                        label={`${run.outcome}${why ? ". " + why : ""}`}
                        end={canRetry(run) ? (
                            <Press label="Retry with the same event" disabled={!editable || (retry.isPending && retry.variables === run.id)} onPress={() => retry.mutate(run.id)} style={{ height: 32, paddingHorizontal: 10, borderRadius: 8, borderWidth: 1, borderColor: color.line2, justifyContent: "center", opacity: editable ? 1 : 0.4 }}>
                                <Text size={13}>{retry.isPending && retry.variables === run.id ? "Sending…" : "Retry"}</Text>
                            </Press>
                        ) : dot ? <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: dot }} /> : null}
                        onPress={open(run)}
                    />
                );
            })}
            {retry.isError ? <Trouble text={retry.error instanceof Error ? retry.error.message : "Couldn’t retry this firing."} /> : null}
            {retry.isSuccess ? <Note tone={color.ink3}>Retry queued. A new entry comes into the history; the one that failed stays as it was.</Note> : null}
        </View>
    );
}

/* ── a new schedule ──────────────────────────────────────────────────── */

const SOMETHING_ELSE = "custom";

/** Putting something new on a clock (`schedule/standing-work.tsx`'s
 *  `NewSchedule`): a time, a row added to a table, or an event on a server
 *  you connected; what it runs; what to tell an agent. The rules are the
 *  web's (`draftProblems`, `createRequest`); the server still has the last
 *  word, and says it here. */
function NewSchedule({ podId, name, back }: { podId: string; name: string; back: string }) {
    const router = useRouter();
    const [draft, setDraft] = useState<ScheduleDraft>(() => ({ ...blankDraft(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }));
    const [tried, setTried] = useState(false);
    const writes = useScheduleWrites(podId);
    const agents = useAgents(podId, name);
    const workflows = useWorkflows(podId);
    const events = useServerEvents(podId, true);
    const tables = useTableList(podId);
    const wrong = draftProblems(draft);
    const change = (patch: Partial<ScheduleDraft>) => setDraft((was) => ({ ...was, ...patch }));

    const targets = useMemo(() => [
        ...(agents.data ?? []).filter((one) => !one.broken).map((one) => ({ value: "agent:" + one.name, label: one.label })),
        ...(workflows.data ?? []).filter((one) => one.active).map((one) => ({ value: "workflow:" + one.name, label: humanizeName(one.name) + " (workflow)" })),
    ], [agents.data, workflows.data]);
    const whens = [...WHENS.map((one) => ({ value: one.value as string, label: one.label })), ...serverGroups(events.data ?? []).flatMap((group) => group.events.map((one) => ({ value: one.key, label: one.event + " · " + group.label.replace(/^From /, "") })))];
    const cadence = CADENCES.some((one) => one.cron === draft.cron) ? draft.cron : SOMETHING_ELSE;

    const field = (one: Partial<FormField> & { name: string; label: string; kind: FormField["kind"] }): FormField => ({ description: null, required: false, options: [], readOnly: false, reference: null, integer: false, ...one });
    const fields: FormField[] = [
        field({ name: "name", label: "Name", kind: "text", required: true, description: "What this row will be called: daily tracker refresh." }),
        field({ name: "when", label: "When", kind: "select", options: whens }),
        ...(draft.when === "record.created" ? [field({ name: "table", label: "Table", kind: "select", options: (tables.data?.pages.flatMap((page) => page.items) ?? []).map((one) => ({ value: one.name, label: one.name })) })] : []),
        ...(draft.when === "server" && draft.serverEvent ? draft.serverEvent.asks.map((ask) => field({
            name: "arg:" + ask.name,
            label: ask.name + (ask.required ? "" : " (optional)"),
            kind: ask.options.length || ask.type === "boolean" ? "select" : "text",
            description: ask.description || null,
            options: (ask.options.length ? ask.options : ask.type === "boolean" ? ["true", "false"] : []).map((one) => ({ value: one, label: one })),
        })) : []),
        ...(draft.when === "time" ? [
            field({ name: "cadence", label: "How often", kind: "select", options: [...CADENCES.map((one) => ({ value: one.cron, label: one.label })), { value: SOMETHING_ELSE, label: "Something else" }] }),
            field({ name: "cron", label: "Cron", kind: "text", description: "What is stored: minute, hour, day of month, month, day of week." }),
            field({ name: "timezone", label: "Time zone", kind: "text" }),
        ] : []),
        field({ name: "target", label: "Run", kind: "select", required: true, options: targets }),
        field({ name: "instruction", label: "Instructions", kind: "textarea", description: "What to do each time it runs. An agent with instructions of its own needs none; a workflow uses its steps." }),
    ];
    const values: Record<string, unknown> = {
        name: draft.name,
        when: draft.when === "server" ? draft.serverEvent?.key ?? "" : draft.when,
        table: draft.table,
        cadence,
        cron: draft.cron,
        timezone: draft.timezone,
        target: draft.target === "workflow" ? (draft.workflowName ? "workflow:" + draft.workflowName : "") : (draft.agentName ? "agent:" + draft.agentName : ""),
        instruction: draft.instruction,
        ...Object.fromEntries(Object.entries(draft.eventArguments).map(([key, value]) => ["arg:" + key, value])),
    };
    const errors: Record<string, string> = tried ? {
        ...(wrong.name ? { name: wrong.name } : {}),
        ...(wrong.table ? { table: wrong.table } : {}),
        ...(wrong.cron ? { cron: wrong.cron } : {}),
        ...(wrong.target ? { target: wrong.target } : {}),
        ...(wrong.event ? { when: wrong.event } : {}),
    } : {};

    const onChange = (key: string, value: unknown) => {
        const said = String(value ?? "");
        if (key === "when") {
            const heard = (events.data ?? []).find((one) => one.key === said);
            change(heard ? { when: "server", serverEvent: heard, eventArguments: {} } : { when: said as ScheduleDraft["when"], serverEvent: null, eventArguments: {} });
        } else if (key === "cadence") {
            if (said !== SOMETHING_ELSE) change({ cron: said });
        } else if (key === "target") {
            const [kind, ...rest] = said.split(":");
            const picked = rest.join(":");
            change(kind === "workflow" ? { target: "workflow", workflowName: picked } : { target: "agent", agentName: picked });
        } else if (key.startsWith("arg:")) {
            change({ eventArguments: { ...draft.eventArguments, [key.slice(4)]: said } });
        } else {
            change({ [key]: said } as Partial<ScheduleDraft>);
        }
    };

    const submit = () => {
        setTried(true);
        if (Object.keys(wrong).length > 0) return;
        writes.create.mutate(createRequest(draft), { onSuccess: () => router.back() });
    };

    return (
        <Screen
            podId={podId}
            back={back}
            foot={(
                <View style={{ flexDirection: "row", gap: 12, paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8, borderTopWidth: 1, borderTopColor: color.line }}>
                    <Button title={writes.create.isPending ? "Setting it up…" : "Create schedule"} grow height={46} busy={writes.create.isPending} onPress={submit} />
                </View>
            )}
        >
            <Title title="New schedule"><Text size={14.5} tone={color.ink2}>Work {name} does without being asked.</Text></Title>
            {events.isError ? <Note tone={color.ink3}>Events from your connected servers could not be read, so only these are offered.</Note> : null}
            <View style={{ paddingHorizontal: 4, paddingTop: 10 }}>
                <Form fields={fields} values={values} errors={errors} onChange={onChange} />
            </View>
            {draft.when === "server" && draft.serverEvent ? <Note tone={color.ink3}>From {draft.serverEvent.server}.{draft.serverEvent.description ? " " + draft.serverEvent.description : ""}</Note> : null}
            {writes.create.isError ? <Problem text={writes.create.error instanceof Error ? writes.create.error.message : "Couldn’t create the schedule."} onDismiss={() => writes.create.reset()} /> : null}
        </Screen>
    );
}
