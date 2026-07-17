'use client';

import { useCallback, useMemo } from 'react';
import { useInfiniteQuery, useQueries, useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { normalizeFlowNodeConfig } from '@/lib/utils/flow-node-config';
import type {
    Workflow,
    WorkflowCreateRequest,
    WorkflowEdge,
    WorkflowGraphUpdateInput,
    WorkflowInstallMode,
    WorkflowNode,
    WorkflowRun,
    WorkflowStart,
    WorkflowUpdateInput,
} from '../types';

type WorkflowRunPage = {
    items: WorkflowRun[];
    next_page_token?: string | null;
};

type WorkflowRunPollingOptions = {
    poll?: boolean;
    pollWhenLive?: boolean;
    pollIntervalMs?: number;
    enabled?: boolean;
    limit?: number;
};

export type WorkflowRunWaitAssignment = {
    run: WorkflowRun;
    wait: {
        id?: string;
        run_id: string;
        workflow_id: string;
        node_id: string;
        wait_type: string;
        status?: string;
        payload?: Record<string, unknown>;
        assigned_pod_member_id?: string | null;
        external_ref?: string | null;
        created_at?: string;
        updated_at?: string;
        completed_at?: string | null;
    };
};

export type WorkflowRunWaitAssignmentPage = {
    items: WorkflowRunWaitAssignment[];
    total?: number;
    next_page_token?: string | null;
};

export type WorkflowRunSnapshot = {
    workflowName: string;
    runs: WorkflowRun[];
};

export const WORKFLOW_RUN_POLL_INTERVAL_MS = 2000;
const WORKFLOW_RUN_LIST_LIMIT = 10;
const WORKFLOW_RUN_ACTIVE_POLL_MAX_AGE_MS = 15 * 60 * 1000;
const WORKFLOW_RUN_WAITING_POLL_MAX_AGE_MS = 5 * 60 * 1000;

const TERMINAL_WORKFLOW_RUN_STATUSES = new Set(['COMPLETED', 'SUCCESS', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'CANCELED']);
const ACTIVE_WORKFLOW_RUN_STATUSES = new Set(['PENDING', 'RUNNING', 'EXECUTING', 'IN_PROGRESS', 'PROCESSING']);
const SUCCESS_WORKFLOW_RUN_STATUSES = new Set(['COMPLETED', 'SUCCESS', 'SUCCEEDED']);

export function normalizeWorkflowRunStatus(status: unknown): string {
    return String(status || '').trim().toUpperCase();
}

export function isTerminalWorkflowRunStatus(status: unknown): boolean {
    return TERMINAL_WORKFLOW_RUN_STATUSES.has(normalizeWorkflowRunStatus(status));
}

export function isSuccessfulWorkflowRunStatus(status: unknown): boolean {
    return SUCCESS_WORKFLOW_RUN_STATUSES.has(normalizeWorkflowRunStatus(status));
}

export function shouldPollWorkflowRun(run?: WorkflowRun | null): boolean {
    if (!run) return false;

    const status = normalizeWorkflowRunStatus(run.status);
    if (!status || isTerminalWorkflowRunStatus(status)) return false;

    if (status === 'WAITING' || status === 'WAITING_FOR_INPUT') {
        // Human form waits only resolve when someone submits — no point polling.
        const waitType = run.active_wait?.wait_type;
        const isWaitingOnRuntime = Boolean(waitType && waitType !== 'HUMAN');
        return isWaitingOnRuntime && wasWorkflowRunRecentlyActive(run, WORKFLOW_RUN_WAITING_POLL_MAX_AGE_MS);
    }

    return ACTIVE_WORKFLOW_RUN_STATUSES.has(status)
        && wasWorkflowRunRecentlyActive(run, WORKFLOW_RUN_ACTIVE_POLL_MAX_AGE_MS);
}

function getWorkflowRunPollInterval(run: WorkflowRun | null | undefined, options: WorkflowRunPollingOptions): number | false {
    if (!options.poll) return false;
    if (run && !shouldPollWorkflowRun(run)) return false;
    return options.pollIntervalMs ?? WORKFLOW_RUN_POLL_INTERVAL_MS;
}

function getWorkflowRunActivityTimestamp(run: WorkflowRun): number | null {
    const candidate = run.updated_at || run.started_at || run.created_at || run.completed_at || '';
    const parsed = Date.parse(candidate);
    return Number.isFinite(parsed) ? parsed : null;
}

function wasWorkflowRunRecentlyActive(run: WorkflowRun, maxAgeMs: number): boolean {
    const timestamp = getWorkflowRunActivityTimestamp(run);
    if (timestamp === null) return true;
    return Date.now() - timestamp <= maxAgeMs;
}

function normalizeInstallMode(value: unknown): WorkflowInstallMode | undefined {
    if (typeof value !== 'string') return undefined;
    const normalized = value.trim().toUpperCase();
    if (normalized === 'GLOBAL' || normalized === 'USER') {
        return normalized as WorkflowInstallMode;
    }
    return undefined;
}

function normalizeFlow(raw: Record<string, unknown>): Workflow {
    const rawNodes = (raw.nodes as WorkflowNode[] | undefined) || [];
    const start = (raw.start as WorkflowStart | undefined) ?? undefined;
    const mode = normalizeInstallMode(raw.mode);

    // List (summary) responses carry derived node_count/node_types instead of the
    // full graph; detail responses carry the full nodes — derive from them there.
    const nodeCount =
        typeof raw.node_count === 'number' ? raw.node_count : rawNodes.length;
    const nodeTypes = Array.isArray(raw.node_types)
        ? raw.node_types.filter((type): type is string => typeof type === 'string')
        : Array.from(new Set(rawNodes.map((node) => node.type).filter(Boolean) as string[]));

    return {
        id: String(raw.id || raw.name || ''),
        pod_id: String(raw.pod_id || ''),
        name: String(raw.name || raw.id || ''),
        description: (raw.description as string | null | undefined) ?? undefined,
        icon_url: (raw.icon_url as string | null | undefined) ?? null,
        nodes: rawNodes.map((node) => ({
            ...node,
            config: normalizeFlowNodeConfig(node.type, node.config),
        })),
        edges: (raw.edges as WorkflowEdge[] | undefined) || [],
        node_count: nodeCount,
        node_types: nodeTypes,
        viewport: (raw.viewport as Record<string, unknown> | undefined) ?? undefined,
        mode,
        visibility: (raw.visibility as Workflow['visibility'] | undefined) ?? undefined,
        allowed_actions: Array.isArray(raw.allowed_actions) ? raw.allowed_actions.filter((action): action is string => typeof action === 'string') : undefined,
        is_active: (raw.is_active as boolean | undefined) ?? undefined,
        start,
        created_at: String(raw.created_at || ''),
        updated_at: String(raw.updated_at || raw.created_at || ''),
    };
}

function normalizeFlowRun(raw: Record<string, unknown>): WorkflowRun {
    return {
        id: String(raw.id || ''),
        workflow_id: String(raw.workflow_id || ''),
        pod_id: String(raw.pod_id || ''),
        user_id: String(raw.user_id || ''),
        trigger_type: (raw.trigger_type as string | undefined) ?? undefined,
        status: ((raw.status as WorkflowRun['status']) || 'PENDING') as WorkflowRun['status'],
        current_node_id: (raw.current_node_id as string | null | undefined) ?? null,
        active_wait: (raw.active_wait as WorkflowRun['active_wait'] | undefined) ?? null,
        error: (raw.error as string | null | undefined) ?? null,
        failed_node_id: (raw.failed_node_id as string | null | undefined) ?? null,
        execution_context: (raw.execution_context as WorkflowRun['execution_context'] | undefined) ?? undefined,
        step_history: (raw.step_history as WorkflowRun['step_history']) ?? undefined,
        started_at: (raw.started_at as string | null | undefined) ?? null,
        completed_at: (raw.completed_at as string | null | undefined) ?? null,
        created_at: String(raw.created_at || ''),
        updated_at: String(raw.updated_at || raw.created_at || ''),
    };
}

function normalizeWait(raw: Record<string, unknown>): WorkflowRunWaitAssignment {
    const run = (raw.run || {}) as Record<string, unknown>;
    const wait = (raw.wait || {}) as Record<string, unknown>;

    return {
        run: normalizeFlowRun(run),
        wait: {
            id: typeof wait.id === 'string' ? wait.id : undefined,
            run_id: String(wait.run_id || run.id || ''),
            workflow_id: String(wait.workflow_id || run.workflow_id || ''),
            node_id: String(wait.node_id || run.current_node_id || ''),
            wait_type: String(wait.wait_type || ''),
            status: typeof wait.status === 'string' ? wait.status : undefined,
            payload: wait.payload && typeof wait.payload === 'object'
                ? wait.payload as Record<string, unknown>
                : undefined,
            assigned_pod_member_id: (wait.assigned_pod_member_id as string | null | undefined) ?? null,
            external_ref: (wait.external_ref as string | null | undefined) ?? null,
            created_at: typeof wait.created_at === 'string' ? wait.created_at : undefined,
            updated_at: typeof wait.updated_at === 'string' ? wait.updated_at : undefined,
            completed_at: (wait.completed_at as string | null | undefined) ?? null,
        },
    };
}

function getRunSortTime(run: WorkflowRun): number {
    const timestamp = run.started_at || run.created_at || run.completed_at || run.updated_at || '';
    const parsed = Date.parse(timestamp);
    if (Number.isFinite(parsed)) return parsed;

    const uuidV7Timestamp = Number.parseInt(run.id.slice(0, 12), 16);
    return Number.isFinite(uuidV7Timestamp) ? uuidV7Timestamp : 0;
}

function sortFlowRuns(runs: WorkflowRun[]): WorkflowRun[] {
    return [...runs].sort((a, b) => {
        return getRunSortTime(b) - getRunSortTime(a);
    });
}

export const flowsQueryOptions = (podId: string | undefined) => ({
        queryKey: ['flows', podId],
        queryFn: async () => {
            const response = await getLemmaClient(podId).workflows.list();
            return (response.items || []).map((item) => normalizeFlow(item as unknown as Record<string, unknown>));
        },
});

export const useFlows = (podId: string | undefined) => {
    return useQuery({
        ...flowsQueryOptions(podId),
        enabled: !!podId,
    });
};

export const useFlow = (podId: string | undefined, workflowName: string | undefined) => {
    return useQuery({
        queryKey: ['flows', podId, workflowName],
        queryFn: async () => {
            const response = await getLemmaClient(podId).workflows.get(workflowName!);
            return normalizeFlow(response as unknown as Record<string, unknown>);
        },
        enabled: !!podId && !!workflowName,
    });
};

export const useCreateFlow = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, data }: { podId: string; data: WorkflowCreateRequest }) => {
            const payload: Record<string, unknown> = {
                name: data.name,
                description: data.description,
                icon_url: data.icon_url,
                visibility: data.visibility,
            };

            if (data.start !== undefined) {
                payload.start = data.start as unknown as never;
            }

            const mode = normalizeInstallMode(data.mode);
            if (mode) {
                payload.mode = mode;
            }

            const response = await getLemmaClient(podId).workflows.create(payload as unknown as never);
            return normalizeFlow(response as unknown as Record<string, unknown>);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['flows', variables.podId] });
        },
    });
};

export const useUpdateFlow = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, id, data }: { podId: string; id: string; data: WorkflowUpdateInput }) => {
            const payload: Record<string, unknown> = {
                description: data.description,
                icon_url: data.icon_url,
                visibility: data.visibility,
            };

            if ('start' in data) {
                payload.start = (data as unknown as { start?: WorkflowStart | null }).start as unknown as never;
            }

            const mode = normalizeInstallMode(data.mode);
            if (mode) {
                payload.mode = mode;
            } else if (data.mode === null) {
                payload.mode = null;
            }

            const response = await getLemmaClient(podId).workflows.update(id, payload as unknown as never);
            return normalizeFlow(response as unknown as Record<string, unknown>);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['flows', variables.podId] });
            queryClient.invalidateQueries({ queryKey: ['flows', variables.podId, variables.id] });
        },
    });
};

export const useUpdateFlowGraph = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, id, data }: { podId: string; id: string; data: WorkflowGraphUpdateInput }) => {
            const payload: Record<string, unknown> = {
                nodes: data.nodes as unknown as never[],
                edges: data.edges as unknown as never[],
            };

            if (data.start !== undefined) {
                payload.start = data.start as unknown as never;
            }

            const response = await getLemmaClient(podId).workflows.graph.update(id, payload as unknown as never);
            return normalizeFlow(response as unknown as Record<string, unknown>);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['flows', variables.podId] });
            queryClient.invalidateQueries({ queryKey: ['flows', variables.podId, variables.id] });
        },
    });
};

export const useDeleteFlow = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, id }: { podId: string; id: string }) =>
            getLemmaClient(podId).workflows.delete(id),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['flows', variables.podId] });
        },
    });
};

// Flow Runs
export const useFlowRuns = (
    podId: string | undefined,
    workflowName: string | undefined,
    options: WorkflowRunPollingOptions = {}
) => {
    const runsQuery = useQuery({
        queryKey: ['flow-runs', podId, workflowName],
        queryFn: async () => {
            const response = await getLemmaClient(podId).workflows.runs.list(workflowName!, {
                limit: options.limit ?? WORKFLOW_RUN_LIST_LIMIT,
            });
            return sortFlowRuns(
                (response.items || []).map((item) => normalizeFlowRun(item as unknown as Record<string, unknown>))
            );
        },
        enabled: options.enabled !== false && !!podId && !!workflowName,
    });

    const liveRunTargets = useMemo(() => {
        if (!workflowName) return [];
        return (runsQuery.data || [])
            .filter(shouldPollWorkflowRun)
            .map((run) => ({ workflowName, run }));
    }, [runsQuery.data, workflowName]);

    const liveRunQueries = useQueries({
        queries: liveRunTargets.map(({ workflowName, run }) => ({
            queryKey: ['flow-runs', podId, workflowName, run.id],
            queryFn: async () => {
                const response = await getLemmaClient(podId).workflows.runs.get(run.id, podId);
                return normalizeFlowRun(response as unknown as Record<string, unknown>);
            },
            enabled: options.enabled !== false && options.pollWhenLive === true && !!podId && !!workflowName && !!run.id,
            initialData: run,
            refetchInterval: (query: { state: { data: WorkflowRun | undefined } }) => getWorkflowRunPollInterval(query.state.data, {
                ...options,
                poll: true,
            }),
        })),
    });

    const liveRunsById = useMemo(() => {
        const map = new Map<string, WorkflowRun>();

        liveRunTargets.forEach(({ run }, index) => {
            map.set(run.id, (liveRunQueries[index]?.data as WorkflowRun | undefined) || run);
        });

        return map;
    }, [liveRunQueries, liveRunTargets]);

    const mergedRuns = useMemo(() => {
        return sortFlowRuns((runsQuery.data || []).map((run) => liveRunsById.get(run.id) || run));
    }, [liveRunsById, runsQuery.data]);

    const refetchRuns = runsQuery.refetch;
    const refetch = useCallback(async () => {
        const runsResult = await refetchRuns();
        await Promise.allSettled(liveRunQueries.map((query) => query.refetch()));
        return runsResult;
    }, [liveRunQueries, refetchRuns]);

    return {
        ...runsQuery,
        data: mergedRuns,
        isLoading: runsQuery.isLoading || liveRunQueries.some((query) => query.isLoading && !query.data),
        isFetching: runsQuery.isFetching || liveRunQueries.some((query) => query.isFetching),
        refetch,
    };
};

export const useWorkflowRunWaitAssignments = (podId: string | undefined, limit = 20) => {
    return useQuery({
        queryKey: ['workflow-run-waits', podId, limit],
        queryFn: async (): Promise<WorkflowRunWaitAssignmentPage> => {
            const response = await getLemmaClient(podId).workflows.runs.waitingAssignedToMe({ limit });
            return {
                items: (response.items || []).map((item) => normalizeWait(item as unknown as Record<string, unknown>)),
                total: (response as { total?: number }).total,
                next_page_token: response.next_page_token ?? null,
            };
        },
        enabled: !!podId,
    });
};

export const useWorkflowRunSnapshots = (
    podId: string | undefined,
    workflowNames: string[],
    limit = 5,
    options: WorkflowRunPollingOptions = {}
) => {
    const snapshotsQuery = useQuery({
        queryKey: ['workflow-run-snapshots', podId, workflowNames.join('\u0000'), limit],
        queryFn: async (): Promise<WorkflowRunSnapshot[]> => {
            const snapshots = await Promise.all(
                workflowNames.map(async (workflowName) => {
                    const response = await getLemmaClient(podId).workflows.runs.list(workflowName, { limit });
                    return {
                        workflowName,
                        runs: sortFlowRuns(
                            (response.items || []).map((item) => normalizeFlowRun(item as unknown as Record<string, unknown>))
                        ),
                    };
                })
            );
            return snapshots;
        },
        enabled: options.enabled !== false && !!podId && workflowNames.length > 0,
    });

    const liveRunTargets = useMemo(() => {
        const seen = new Set<string>();
        const targets: Array<{ workflowName: string; run: WorkflowRun }> = [];

        for (const snapshot of snapshotsQuery.data || []) {
            for (const run of snapshot.runs) {
                if (!shouldPollWorkflowRun(run)) continue;
                if (seen.has(run.id)) continue;
                seen.add(run.id);
                targets.push({ workflowName: snapshot.workflowName, run });
            }
        }

        return targets;
    }, [snapshotsQuery.data]);

    const liveRunQueries = useQueries({
        queries: liveRunTargets.map(({ workflowName, run }) => ({
            queryKey: ['flow-runs', podId, workflowName, run.id],
            queryFn: async () => {
                const response = await getLemmaClient(podId).workflows.runs.get(run.id, podId);
                return normalizeFlowRun(response as unknown as Record<string, unknown>);
            },
            enabled: options.enabled !== false && options.pollWhenLive === true && !!podId && !!workflowName && !!run.id,
            initialData: run,
            refetchInterval: (query: { state: { data: WorkflowRun | undefined } }) => getWorkflowRunPollInterval(query.state.data, {
                ...options,
                poll: true,
            }),
        })),
    });

    const liveRunsById = useMemo(() => {
        const map = new Map<string, WorkflowRun>();

        liveRunTargets.forEach(({ run }, index) => {
            map.set(run.id, (liveRunQueries[index]?.data as WorkflowRun | undefined) || run);
        });

        return map;
    }, [liveRunQueries, liveRunTargets]);

    const mergedSnapshots = useMemo(() => {
        return (snapshotsQuery.data || []).map((snapshot) => ({
            ...snapshot,
            runs: sortFlowRuns(snapshot.runs.map((run) => liveRunsById.get(run.id) || run)),
        }));
    }, [liveRunsById, snapshotsQuery.data]);

    const refetchSnapshots = snapshotsQuery.refetch;
    const refetch = useCallback(async () => {
        const snapshotResult = await refetchSnapshots();
        await Promise.allSettled(liveRunQueries.map((query) => query.refetch()));
        return snapshotResult;
    }, [liveRunQueries, refetchSnapshots]);

    return {
        ...snapshotsQuery,
        data: mergedSnapshots,
        isLoading: snapshotsQuery.isLoading || liveRunQueries.some((query) => query.isLoading && !query.data),
        isFetching: snapshotsQuery.isFetching || liveRunQueries.some((query) => query.isFetching),
        refetch,
    };
};

export const useInfiniteFlowRuns = (
    podId: string | undefined,
    workflowName: string | undefined,
    limit = WORKFLOW_RUN_LIST_LIMIT,
    options: WorkflowRunPollingOptions = {}
) => {
    const runsQuery = useInfiniteQuery({
        queryKey: ['flow-runs', 'infinite', podId, workflowName, limit],
        queryFn: async ({ pageParam }): Promise<WorkflowRunPage> => {
            const response = await getLemmaClient(podId).workflows.runs.list(workflowName!, {
                limit,
                pageToken: pageParam || undefined,
            });
            return {
                items: sortFlowRuns(
                    (response.items || []).map((item) => normalizeFlowRun(item as unknown as Record<string, unknown>))
                ),
                next_page_token: response.next_page_token ?? null,
            };
        },
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (lastPage) => lastPage.next_page_token || undefined,
        enabled: options.enabled !== false && !!podId && !!workflowName,
    });

    const liveRunTargets = useMemo(() => {
        if (!workflowName) return [];

        const seen = new Set<string>();
        const targets: Array<{ workflowName: string; run: WorkflowRun }> = [];
        const runs = (runsQuery.data?.pages || []).flatMap((page) => page.items);

        for (const run of runs) {
            if (!shouldPollWorkflowRun(run)) continue;
            if (seen.has(run.id)) continue;
            seen.add(run.id);
            targets.push({ workflowName, run });
        }

        return targets;
    }, [runsQuery.data?.pages, workflowName]);

    const liveRunQueries = useQueries({
        queries: liveRunTargets.map(({ workflowName, run }) => ({
            queryKey: ['flow-runs', podId, workflowName, run.id],
            queryFn: async () => {
                const response = await getLemmaClient(podId).workflows.runs.get(run.id, podId);
                return normalizeFlowRun(response as unknown as Record<string, unknown>);
            },
            enabled: options.enabled !== false && options.pollWhenLive === true && !!podId && !!workflowName && !!run.id,
            initialData: run,
            refetchInterval: (query: { state: { data: WorkflowRun | undefined } }) => getWorkflowRunPollInterval(query.state.data, {
                ...options,
                poll: true,
            }),
        })),
    });

    const liveRunsById = useMemo(() => {
        const map = new Map<string, WorkflowRun>();

        liveRunTargets.forEach(({ run }, index) => {
            map.set(run.id, (liveRunQueries[index]?.data as WorkflowRun | undefined) || run);
        });

        return map;
    }, [liveRunQueries, liveRunTargets]);

    const mergedData = useMemo(() => {
        if (!runsQuery.data) return runsQuery.data;

        return {
            ...runsQuery.data,
            pages: runsQuery.data.pages.map((page) => ({
                ...page,
                items: sortFlowRuns(page.items.map((run) => liveRunsById.get(run.id) || run)),
            })),
        };
    }, [liveRunsById, runsQuery.data]);

    const refetchRuns = runsQuery.refetch;
    const refetch = useCallback(async () => {
        const runsResult = await refetchRuns();
        await Promise.allSettled(liveRunQueries.map((query) => query.refetch()));
        return runsResult;
    }, [liveRunQueries, refetchRuns]);

    return {
        ...runsQuery,
        data: mergedData,
        isLoading: runsQuery.isLoading || liveRunQueries.some((query) => query.isLoading && !query.data),
        isFetching: runsQuery.isFetching || liveRunQueries.some((query) => query.isFetching),
        refetch,
    };
};

export const useFlowRun = (
    podId: string | undefined,
    workflowName: string | undefined,
    runId: string | undefined,
    options: WorkflowRunPollingOptions = {}
) => {
    return useQuery({
        queryKey: ['flow-runs', podId, workflowName, runId],
        queryFn: async () => {
            const response = await getLemmaClient(podId).workflows.runs.get(runId!, podId);
            return normalizeFlowRun(response as unknown as Record<string, unknown>);
        },
        enabled: options.enabled !== false && !!podId && !!workflowName && !!runId,
        refetchInterval: (query) => getWorkflowRunPollInterval(query.state.data as WorkflowRun | undefined, options),
    });
};

export const useRunFlow = () => {
    const queryClient = useQueryClient();

    return useMutation({
        // Runs take no inputs; a form-entry workflow comes back WAITING with
        // active_wait so the form can render straight from this response.
        mutationFn: async ({ podId, flowId }: { podId: string; flowId: string }) => {
            const response = await getLemmaClient(podId).workflows.runs.create(flowId);
            return normalizeFlowRun(response as unknown as Record<string, unknown>);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['flow-runs', variables.podId, variables.flowId] });
        },
    });
};

export const useSubmitFlowInput = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ podId, runId, nodeId, data }: { podId: string; flowId: string; runId: string; nodeId: string; data: Record<string, unknown> }) => {
            const response = await getLemmaClient(podId).workflows.runs.submitForm(runId, {
                node_id: nodeId,
                inputs: data,
            }, podId);
            return normalizeFlowRun(response as unknown as Record<string, unknown>);
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['flow-runs', variables.podId, variables.flowId, variables.runId] });
        },
    });
};

// Flow Visualization Hooks
export const useVisualizeFlow = (podId: string | undefined, flowId: string | undefined) => {
    return useQuery({
        queryKey: ['flows', 'visualize', podId, flowId],
        queryFn: () => getLemmaClient(podId).workflows.visualize(flowId!),
        enabled: !!podId && !!flowId,
    });
};

export const useVisualizeFlowRun = (podId: string | undefined, flowId: string | undefined, runId: string | undefined) => {
    return useQuery({
        queryKey: ['flow-runs', 'visualize', podId, flowId, runId],
        queryFn: () => getLemmaClient(podId).workflows.runs.visualize(runId!, podId),
        enabled: !!podId && !!flowId && !!runId,
    });
};

// Flow Run Management Hooks
export const useCancelFlowRun = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, runId }: { podId: string; flowId: string; runId: string }) =>
            getLemmaClient(podId).workflows.runs.cancel(runId, podId) as unknown as Promise<WorkflowRun>,
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['flow-runs', variables.podId, variables.flowId] });
            queryClient.invalidateQueries({ queryKey: ['flow-runs', variables.podId, variables.flowId, variables.runId] });
        },
    });
};
