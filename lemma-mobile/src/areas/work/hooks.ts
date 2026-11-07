import { useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useIsFocused } from "expo-router";
import { useCacheKey, useMe, useOrg } from "../../data/hooks";
import { isPodDefaultAgent } from "@web/data/agent-names";
import { useArea } from "../use-area";
import { planToWrite, PlanMovedOn, stepNames } from "./model";
import { answeringAs, type AgentDetail, type AgentDraft } from "@web/data/agents";
import { parsePlan, type Plan } from "./web/plan";
import { readGraph } from "@web/workflow/run-tree";
import { stillGoing, type RunDetail, type RunRow } from "@web/workflow/runs";
import { readShape } from "@web/workflow/shape";
import { automationOf } from "@web/workflow/turn-on";
import type { StandingJob } from "@web/schedule/schedules";

/** The work screens' reads and writes, through react-query. Every key
 *  starts with `useCacheKey()`, so sample data and each server keep their own
 *  cache entries.
 *
 *  Live updates are the web's: a run is pushed by its own stream while it is
 *  going (`watchRun`), and lists are read again while something in them is
 *  moving. Neither runs while the screen is behind another, as the web's stop
 *  behind a hidden pane. */

export function useWorkSource() {
    return useArea("work");
}

/** The keys, written once, so a write refreshes exactly what reads it. */
export const workKeys = {
    all: (key: string, podId: string) => [key, "work", podId] as const,
    workflows: (key: string, podId: string) => [key, "work", podId, "workflows"] as const,
    workflow: (key: string, podId: string, name: string) => [key, "work", podId, "workflow", name] as const,
    recent: (key: string, podId: string) => [key, "work", podId, "runs", "recent"] as const,
    flowRuns: (key: string, podId: string, name: string) => [key, "work", podId, "runs", "of", name] as const,
    inFlight: (key: string, podId: string, workflowId: string) => [key, "work", podId, "runs", "in-flight", workflowId] as const,
    waiting: (key: string, podId: string) => [key, "work", podId, "runs", "waiting"] as const,
    run: (key: string, podId: string, runId: string) => [key, "work", podId, "run", runId] as const,
    schedules: (key: string, podId: string) => [key, "work", podId, "schedules"] as const,
    scheduleRuns: (key: string, podId: string, id: string) => [key, "work", podId, "schedules", id, "runs"] as const,
    agents: (key: string, podId: string) => [key, "work", podId, "agents"] as const,
    agent: (key: string, podId: string, name: string) => [key, "work", podId, "agent", name] as const,
    plan: (key: string, podId: string) => [key, "work", podId, "plan"] as const,
};

/** How often a list of runs is read again (the web's `runsPollEvery`): often
 *  while something in it moves, a minute while it only waits on a person. */
export function runsPollEvery(runs: readonly RunRow[] | undefined, moving: number): number | false {
    if (runs?.some((run) => run.status === "PENDING" || run.status === "RUNNING")) return moving;
    if (runs?.some((run) => run.status === "WAITING")) return 60_000;
    return false;
}

/** The reader's user id, for "yours" and "by you". */
export function useMyId(): string | null {
    const me = useMe();
    return me.data?.id ?? null;
}

/* ── workflows ───────────────────────────────────────────────────────── */

export function useWorkflows(podId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({ queryKey: workKeys.workflows(key, podId), queryFn: () => source.workflows(podId), staleTime: 5 * 60_000 });
}

/** One workflow as stored, with the web's three readings of it. */
export function useWorkflow(podId: string, name: string | null) {
    const source = useWorkSource();
    const key = useCacheKey();
    const query = useQuery({
        queryKey: workKeys.workflow(key, podId, name ?? ""),
        queryFn: () => source.workflow(podId, name!),
        enabled: Boolean(name),
        staleTime: 5 * 60_000,
    });
    const read = useMemo(() => {
        const raw = query.data ?? null;
        const stored = raw && typeof raw === "object" ? (raw as { start?: unknown; mode?: string; description?: string | null }) : null;
        return { shape: readShape(raw), graph: readGraph(raw), automation: automationOf(stored?.start), perPerson: stored?.mode === "USER", description: stored?.description ?? null };
    }, [query.data]);
    return { query, ...read };
}

/** Each named workflow's steps by id, for run rows that span workflows:
 *  "At Send the reply" rather than "At reply". Read from the same cache the
 *  workflow's own page reads. */
export function useStepNames(podId: string, names: readonly string[]): Map<string, ReadonlyMap<string, string>> {
    const source = useWorkSource();
    const key = useCacheKey();
    const wanted = [...new Set(names)].sort();
    return useQueries({
        queries: wanted.map((name) => ({
            queryKey: workKeys.workflow(key, podId, name),
            queryFn: () => source.workflow(podId, name),
            staleTime: 5 * 60_000,
        })),
        combine: (results) => new Map(wanted.map((name, index) => [name, stepNames(readShape(results[index]?.data ?? null))] as const)),
    });
}

/** The Lemma's latest runs, every workflow's, newest first: what the Runs
 *  tab's Running and History views are drawn from. */
export const RECENT_RUNS = 100;

export function useRecentRuns(podId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const focused = useIsFocused();
    return useQuery({
        queryKey: workKeys.recent(key, podId),
        queryFn: () => source.recentRuns(podId, RECENT_RUNS),
        staleTime: 15_000,
        refetchInterval: (query) => (focused ? runsPollEvery(query.state.data, 15_000) : false),
    });
}

export function useWorkflowRuns(podId: string, name: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const focused = useIsFocused();
    return useInfiniteQuery({
        queryKey: workKeys.flowRuns(key, podId, name),
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam }) => source.workflowRuns(podId, name, pageParam),
        getNextPageParam: (page) => page.next ?? undefined,
        staleTime: 15_000,
        refetchInterval: (query) => (focused ? runsPollEvery(query.state.data?.pages.flatMap((page) => page.items), 20_000) : false),
    });
}

export function useRunsInFlight(podId: string, workflowId: string | null) {
    const source = useWorkSource();
    const key = useCacheKey();
    const focused = useIsFocused();
    return useQuery({
        queryKey: workKeys.inFlight(key, podId, workflowId ?? ""),
        queryFn: () => source.runsInFlight(podId, workflowId!),
        enabled: Boolean(workflowId),
        staleTime: 15_000,
        refetchInterval: focused ? 20_000 : false,
    });
}

/** The forms waiting on the reader here, with their runs. */
export function useWaitingOnMe(podId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const focused = useIsFocused();
    return useQuery({
        queryKey: workKeys.waiting(key, podId),
        queryFn: () => source.waitingOnMe(podId),
        staleTime: 15_000,
        refetchInterval: focused ? 20_000 : false,
    });
}

/** How often one run is read again while nothing is pushed: a form can sit
 *  for hours, anything else going is looked at often (`use-run.ts`). */
function pollEvery(detail: RunDetail | null | undefined): number | false {
    if (!detail || !stillGoing(detail.status)) return false;
    return detail.wait?.type === "HUMAN" ? 15_000 : 6_000;
}

/** One run, kept current: pushed whole on every change while it is going,
 *  and read again only while the push is not arriving. */
export function useRun(podId: string, runId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const focused = useIsFocused();
    const [streaming, setStreaming] = useState(false);
    const run = useQuery({
        queryKey: workKeys.run(key, podId, runId),
        queryFn: () => source.run(podId, runId),
        /* No id is a run not asked for yet. */
        enabled: Boolean(runId),
        staleTime: 5_000,
        refetchInterval: (query) => (focused && !streaming ? pollEvery(query.state.data) : false),
    });
    const going = Boolean(run.data && stillGoing(run.data.status));
    const watch = source.watchRun;
    useEffect(() => {
        if (!watch || !going || !focused) return;
        const controller = new AbortController();
        void (async () => {
            try {
                await watch(podId, runId, (detail) => {
                    cache.setQueryData(workKeys.run(key, podId, runId), detail);
                    setStreaming(true);
                }, controller.signal);
            } catch {
                /* The stream is a nicety; reading again carries on without it. */
            } finally {
                if (!controller.signal.aborted) setStreaming(false);
            }
        })();
        return () => {
            controller.abort();
            setStreaming(false);
        };
    }, [watch, going, focused, podId, runId, key, cache]);
    return { ...run, streaming };
}

/** After a run moves by the reader's hand: the run, the lists, the forms
 *  waiting, and Home's queue all read again. */
export function refreshRuns(cache: QueryClient, key: string, podId: string, runId?: string) {
    if (runId) void cache.invalidateQueries({ queryKey: workKeys.run(key, podId, runId) });
    void cache.invalidateQueries({ queryKey: [key, "work", podId, "runs"] });
    void cache.invalidateQueries({ queryKey: [key, "pulse"] });
}

export function useStartRun(podId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (name: string) => source.startRun(podId, name),
        onSuccess: () => refreshRuns(cache, key, podId),
    });
}

export function useRunActions(podId: string, runId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const done = () => refreshRuns(cache, key, podId, runId);
    return {
        cancel: useMutation({ mutationFn: () => source.cancelRun(podId, runId), onSuccess: done }),
        retry: useMutation({ mutationFn: () => source.retryRun(podId, runId), onSuccess: done }),
    };
}

export function useSubmitForm(podId: string, runId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: ({ nodeId, inputs }: { nodeId: string; inputs: Record<string, unknown> }) => source.submitForm(podId, runId, nodeId, inputs),
        onSuccess: () => refreshRuns(cache, key, podId, runId),
    });
}

/* ── schedules ───────────────────────────────────────────────────────── */

/** The Lemma's schedules, its own agent's named as the Lemma. */
export function useSchedules(podId: string, lemmaName: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: workKeys.schedules(key, podId),
        queryFn: () => source.schedules(podId),
        staleTime: 30_000,
        select: (jobs: StandingJob[]) => jobs.map((job) => (job.target.kind === "agent" && isPodDefaultAgent(job.target.name) ? { ...job, target: { ...job.target, label: lemmaName } } : job)),
    });
}

export function useScheduleRuns(podId: string, scheduleId: string, skipped: boolean) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: [...workKeys.scheduleRuns(key, podId, scheduleId), skipped ? "skipped" : "fired"],
        queryFn: () => source.scheduleRuns(podId, scheduleId, skipped),
        enabled: Boolean(scheduleId),
        staleTime: 15_000,
    });
}

/** Writes to a schedule. The list is read again rather than patched:
 *  resuming one the breaker stopped clears its failure count on the server,
 *  and a patched row would keep saying it failed five times. */
export function useScheduleWrites(podId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const done = () => void cache.invalidateQueries({ queryKey: workKeys.schedules(key, podId) });
    return {
        active: useMutation({ mutationFn: ({ id, active }: { id: string; active: boolean }) => source.setScheduleActive(podId, id, active), onSuccess: done }),
        visibility: useMutation({ mutationFn: ({ id, visibility }: { id: string; visibility: "POD" | "PERSONAL" }) => source.setScheduleVisibility(podId, id, visibility), onSuccess: done }),
        create: useMutation({ mutationFn: (body: Record<string, unknown>) => source.createSchedule(podId, body), onSuccess: done }),
        remove: useMutation({ mutationFn: (id: string) => source.deleteSchedule(podId, id), onSuccess: done }),
    };
}

/** Run a failed firing again with the event it carried. The old row stays
 *  failed: a retry is a second attempt in the history, not an edit. */
export function useRetryFiring(podId: string, scheduleId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (runId: string) => source.retryScheduleRun(podId, scheduleId, runId),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: workKeys.scheduleRuns(key, podId, scheduleId) });
            void cache.invalidateQueries({ queryKey: workKeys.schedules(key, podId) });
        },
    });
}

export function useServerEvents(podId: string, enabled: boolean) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "work", podId, "server-events"], queryFn: () => source.serverEvents(podId), enabled, staleTime: 60_000 });
}

/** The Lemma's connectors, for a workflow that listens to an app. */
export function usePodConnectors(podId: string, enabled: boolean) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "work", podId, "pod-connectors"], queryFn: () => source.podConnectors(podId), enabled, staleTime: 60_000 });
}

/* ── agents ──────────────────────────────────────────────────────────── */

export function useAgents(podId: string, lemmaName: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({ queryKey: workKeys.agents(key, podId), queryFn: () => source.agents(podId), staleTime: 60_000, select: (rows) => answeringAs(rows, lemmaName) });
}

export function useAgent(podId: string, name: string, lemmaName: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: workKeys.agent(key, podId, name),
        queryFn: () => source.agent(podId, name),
        staleTime: 30_000,
        select: (detail: AgentDetail) => answeringAs([detail], lemmaName)[0],
    });
}

export function useAgentWrites(podId: string, name: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return {
        save: useMutation({
            mutationFn: ({ before, after }: { before: AgentDraft; after: AgentDraft }) => source.updateAgent(podId, name, before, after),
            onSuccess: (saved) => {
                cache.setQueryData(workKeys.agent(key, podId, name), saved);
                void cache.invalidateQueries({ queryKey: workKeys.agents(key, podId) });
            },
        }),
        remove: useMutation({
            mutationFn: () => source.deleteAgent(podId, name),
            onSuccess: () => {
                void cache.invalidateQueries({ queryKey: workKeys.agents(key, podId) });
                void cache.invalidateQueries({ queryKey: [key, "work", podId, "surfaces"] });
            },
        }),
    };
}

export function useAgentChats(podId: string, agent: { name: string; front: boolean } | null, enabled: boolean) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: [key, "work", podId, "agent-chats", agent?.name],
        queryFn: () => source.agentChats(podId, agent!),
        enabled: enabled && Boolean(agent),
        staleTime: 30_000,
    });
}

/** The organization's runtimes, for what an agent runs on and what it takes. */
export function useRuntimes(enabled: boolean) {
    const source = useWorkSource();
    const key = useCacheKey();
    const { org } = useOrg();
    return useQuery({ queryKey: [key, "work", "runtimes", org?.id], queryFn: () => source.runtimes(org!.id), enabled: enabled && Boolean(org), staleTime: 5 * 60_000 });
}

/** One runtime read on its own: a coding agent's options are only there. */
export function useRuntime(runtimeId: string | null) {
    const source = useWorkSource();
    const key = useCacheKey();
    const { org } = useOrg();
    return useQuery({ queryKey: [key, "work", "runtime", org?.id, runtimeId], queryFn: () => source.runtime(org!.id, runtimeId!), enabled: Boolean(org && runtimeId), staleTime: 60_000 });
}

export function useSurfaces(podId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "work", podId, "surfaces"], queryFn: () => source.surfaces(podId), staleTime: 5 * 60_000, retry: false });
}

export function useMembers(podId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({ queryKey: [key, "work", podId, "members"], queryFn: () => source.members(podId), staleTime: 5 * 60_000, retry: false });
}

/* ── the plan ────────────────────────────────────────────────────────── */

/** `/plan.md`, read as the `/o` frame reads it: null when there is none,
 *  an error (and never null) when it could not be read. */
export function usePlan(podId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: workKeys.plan(key, podId),
        queryFn: async (): Promise<Plan | null> => {
            const text = await source.planText(podId);
            return text === null ? null : { ...parsePlan(text), file: text };
        },
        staleTime: 5 * 60_000,
    });
}

/** Save the plan. An existing file is read again first and edited in place
 *  only if it still says what the plan was read from; otherwise nothing is
 *  written and the plan is read again (`PlanMovedOn`). */
export function useSavePlan(podId: string) {
    const source = useWorkSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: async ({ plan, exists }: { plan: Plan; exists: boolean }): Promise<Plan> => {
            const current = exists ? await source.planText(podId) : null;
            /* A file gone since it was read has moved on too. */
            const text = planToWrite(plan, current, exists);
            await source.writePlan(podId, text, exists);
            /* What was written is what the next save compares with, as on
               the web. Not read back: a read straight after the write was
               seen to answer the file as it was before it. */
            return { ...plan, file: text };
        },
        onSuccess: (saved) => {
            cache.setQueryData(workKeys.plan(key, podId), saved);
            void cache.invalidateQueries({ queryKey: [key, "owns", podId] });
        },
        onError: (problem) => {
            if (problem instanceof PlanMovedOn) void cache.invalidateQueries({ queryKey: workKeys.plan(key, podId) });
        },
    });
}
