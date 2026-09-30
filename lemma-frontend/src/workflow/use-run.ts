"use client";

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { parseSSEJson, readSSE } from "lemma-sdk";
import { source } from "@/data";
import { lemma } from "@/session/client";
import { readRunDetail, readRuns, readWorkflows, stillGoing, type RunDetail, type RunRow } from "./runs";
import { readGraph } from "./run-tree";

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
                const { SAMPLE_RUN_DETAIL } = await import("@/data/fixtures");
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
                const { SAMPLE_WORKFLOWS } = await import("@/data/fixtures");
                return readWorkflows({ items: SAMPLE_WORKFLOWS });
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
                const { SAMPLE_WORKFLOW_SHAPES } = await import("@/data/fixtures");
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
                const { SAMPLE_WORKFLOW_RUNS } = await import("@/data/fixtures");
                return readRuns({ items: Object.values(SAMPLE_WORKFLOW_RUNS).flat() });
            }
            return readRuns(await lemma(podId).request("GET", "/pods/" + podId + "/workflow-runs", { params: { limit: 100 } }));
        },
        refetchInterval: (query) => (query.state.data?.some((run) => stillGoing(run.status)) ? 15_000 : false),
    });
}
