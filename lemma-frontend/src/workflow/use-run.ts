"use client";

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { parseSSEJson, readSSE, WorkflowRunStatus } from "lemma-sdk";
import { source } from "@/data";
import { lemma } from "@/session/client";
import { readAssignments, readRunDetail, readRuns, readWorkflows, stillGoing, type RunDetail, type RunRow, type WaitRow } from "./runs";
import { readGraph } from "./run-tree";
import { samples } from "@/data/samples";

/** How often to look again, when nothing is pushed.
 *
 *  A run waiting on a person can sit for hours and the answer can come from
 *  somebody else's screen, so it is looked at every quarter-minute rather than
 *  never. Anything else still going is looked at often, because the stream
 *  below is best effort and this is the fallback that is not. */
function pollEvery(detail: RunDetail | null | undefined): number | false {
    if (!detail || !stillGoing(detail.status)) return false;
    return detail.wait?.type === "HUMAN" ? 15_000 : 6_000;
}

/** One run, kept current: the server pushes the whole run on every change
 *  (`GET …/workflow-runs/{id}/stream`), and polling covers a dropped stream. */
export function useRun(podId: string, runId: string) {
    const cache = useQueryClient();
    const sample = source.label === "sample";
    const key = ["workflow-run", podId, runId] as const;
    const run = useQuery({
        queryKey: key,
        queryFn: async () => {
            if (sample) {
                const { SAMPLE_RUN_DETAIL } = await samples(podId);
                return readRunDetail(SAMPLE_RUN_DETAIL[runId] ?? null);
            }
            return readRunDetail(await lemma(podId).workflows.runs.get(runId, podId));
        },
        staleTime: 5_000,
        refetchInterval: (query) => pollEvery(query.state.data),
    });

    const going = Boolean(run.data && stillGoing(run.data.status));
    useEffect(() => {
        if (sample || !going) return;
        const controller = new AbortController();
        void (async () => {
            try {
                const stream = await lemma(podId).stream("/pods/" + podId + "/workflow-runs/" + runId + "/stream", { signal: controller.signal });
                for await (const frame of readSSE(stream)) {
                    if (controller.signal.aborted) return;
                    const parsed = parseSSEJson<{ type?: string; data?: unknown }>(frame);
                    const detail = readRunDetail(parsed?.data);
                    if (detail) cache.setQueryData(key, detail);
                    if (parsed?.type === "completed") break;
                }
            } catch {
                /* The stream is a nicety; polling carries on without it. */
            }
        })();
        return () => controller.abort();
    }, [podId, runId, going, sample, cache]);

    return run;
}

/** Every workflow here, by name and id. A run names its workflow by id only. */
export function useWorkflowList(podId: string) {
    return useQuery({
        queryKey: ["workflows", podId, "names"],
        staleTime: 5 * 60_000,
        queryFn: async () => {
            if (source.label === "sample") {
                const { SAMPLE_WORKFLOWS, hiredHere } = await samples(podId);
                return readWorkflows({ items: hiredHere(podId) ? [] : SAMPLE_WORKFLOWS });
            }
            return readWorkflows(await lemma(podId).workflows.list({ limit: 100 }));
        },
    });
}

/** One workflow's graph, with the raw payload beside it for the start line. */
/** One workflow's definition, as a query. Shared so anything derived from it
 *  (the shape on the workflow page) waits on — and fails with — the same
 *  request instead of sitting disabled behind it. */
export function workflowGraphQuery(podId: string, name: string | null) {
    return {
        queryKey: ["workflow-graph", podId, name] as const,
        staleTime: 5 * 60_000,
        queryFn: async () => {
            let raw: unknown;
            if (source.label === "sample") {
                const { SAMPLE_WORKFLOW_SHAPES } = await samples(podId);
                raw = SAMPLE_WORKFLOW_SHAPES[name!] ?? null;
            } else {
                raw = await lemma(podId).workflows.get(name!);
            }
            return { raw, graph: readGraph(raw) };
        },
    };
}

export function useWorkflowGraph(podId: string, name: string | null) {
    return useQuery({ ...workflowGraphQuery(podId, name), enabled: Boolean(name) });
}

/** Recent runs across every workflow in the space, newest first — one
 *  request (`GET /pods/{id}/workflow-runs`) rather than one per workflow. */
export function useSpaceRuns(podId: string) {
    return useQuery({
        queryKey: ["workflow-runs", podId, "all"],
        staleTime: 15_000,
        queryFn: async (): Promise<RunRow[]> => {
            if (source.label === "sample") {
                const { SAMPLE_WORKFLOW_RUNS, hiredHere } = await samples(podId);
                return readRuns({ items: hiredHere(podId) ? [] : Object.values(SAMPLE_WORKFLOW_RUNS).flat() });
            }
            return readRuns(await lemma(podId).request("GET", "/pods/" + podId + "/workflow-runs", { params: { limit: 100 } }));
        },
        refetchInterval: (query) => (query.state.data?.some((run) => stillGoing(run.status)) ? 15_000 : false),
    });
}

/** One workflow's runs still going — what the board is drawn from.
 *
 *  The space-wide list rather than the workflow's own, because it is the one
 *  that filters by status and by workflow: the workflow's list is newest first
 *  and unfiltered, so a run started a fortnight ago and still stuck sits pages
 *  deep behind finished ones — exactly the run a board exists to show. Each
 *  run comes back with its `title` and `waiting_on`. */
export function useRunsInFlight(podId: string, workflowId: string | null) {
    return useQuery({
        queryKey: ["workflow-runs", podId, "in-flight", workflowId],
        enabled: Boolean(workflowId),
        staleTime: 15_000,
        refetchInterval: 20_000,
        queryFn: async (): Promise<RunRow[]> => {
            if (source.label === "sample") {
                const { SAMPLE_WORKFLOW_RUNS, hiredHere } = await samples(podId);
                return readRuns({ items: hiredHere(podId) ? [] : Object.values(SAMPLE_WORKFLOW_RUNS).flat() })
                    .filter((run) => stillGoing(run.status) && run.workflowId === workflowId);
            }
            return readRuns(await lemma(podId).workflows.runs.listInPod({
                status: [WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING, WorkflowRunStatus.WAITING],
                workflowId: workflowId!,
                limit: 200,
            }));
        },
    });
}

/** The waits in this space assigned to the person looking, by run id.
 *
 *  The board's only way to say "this one is yours" until a run summary names
 *  its assignee: a summary carries no wait, and this endpoint is the one that
 *  answers for the caller. Shares the inbox's cache prefix, so answering a
 *  form from either refreshes both. */
export function useMyWaits(podId: string) {
    return useQuery({
        queryKey: ["workflow-waiting", "board", podId],
        staleTime: 30_000,
        queryFn: async (): Promise<Map<string, WaitRow>> => {
            const list = source.label === "sample"
                ? readAssignments({ items: (await samples(podId)).SAMPLE_WAITING })
                : readAssignments(await lemma(podId).workflows.runs.waitingAssignedToMe({ limit: 100 }));
            return new Map(list.map((one) => [one.run.id, one.wait]));
        },
    });
}
