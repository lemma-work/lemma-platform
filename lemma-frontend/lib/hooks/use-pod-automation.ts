'use client';

import { useMemo } from 'react';

import { usePodSurfaces } from './use-pod-surfaces';
import { useSchedules } from './use-schedules';
import { getScheduleTargetKind } from '@/lib/utils/schedules';
import { surfaceReachesAgent, surfaceUsesDefaultAgent } from '@/lib/utils/surfaces';
import type { AssistantSurface, Schedule } from '@/lib/types';

// One pod-wide schedule query, keyed identically to the schedules page and
// agents list ({ limit: 100 }) so all three views share a single React Query
// cache entry rather than each firing its own filtered request. Grouping by
// agent/workflow then happens client-side — no per-agent N+1 fetches.
const POD_SCHEDULES_FILTER = { limit: 100 } as const;

export { surfaceChannelAgents, surfaceReachesAgent, surfaceUsesDefaultAgent } from '@/lib/utils/surfaces';

export interface PodAutomation {
    schedules: Schedule[];
    surfaces: AssistantSurface[];
    isLoading: boolean;
    isError: boolean;
    /** Active + paused schedules targeting a given agent. */
    schedulesForAgent: (agentName: string) => Schedule[];
    /** Active + paused schedules targeting a given workflow. */
    schedulesForWorkflow: (workflowName: string) => Schedule[];
    /** Surfaces where an agent answers — as default responder or channel route. */
    surfacesForAgent: (agentName: string) => AssistantSurface[];
    /** Surfaces that fall to the pod default assistant (Super Agent). */
    defaultSurfaces: AssistantSurface[];
}

/**
 * Shared read layer for the schedules + surfaces a pod owns. Fetches each list
 * once (pod-wide) and exposes client-side grouping so agent/workflow detail
 * pages, the agents list, and the Pod Assistant view all read from the same
 * cache. Pass `{ schedules: false }` / `{ surfaces: false }` to skip a fetch the
 * caller has no permission for (or does not need).
 */
export function usePodAutomation(
    podId: string | undefined,
    options: { schedules?: boolean; surfaces?: boolean } = {},
): PodAutomation {
    const { schedules: enableSchedules = true, surfaces: enableSurfaces = true } = options;

    const schedulesQuery = useSchedules(enableSchedules ? podId : undefined, POD_SCHEDULES_FILTER);
    const surfacesQuery = usePodSurfaces(enableSurfaces ? podId : undefined);

    const schedules = useMemo(() => schedulesQuery.data?.items ?? [], [schedulesQuery.data?.items]);
    const surfaces = useMemo(() => surfacesQuery.data ?? [], [surfacesQuery.data]);

    return useMemo<PodAutomation>(() => ({
        schedules,
        surfaces,
        isLoading:
            (enableSchedules && schedulesQuery.isLoading) ||
            (enableSurfaces && surfacesQuery.isLoading),
        isError:
            (enableSchedules && schedulesQuery.isError) ||
            (enableSurfaces && surfacesQuery.isError),
        schedulesForAgent: (agentName) =>
            schedules.filter(
                (schedule) => getScheduleTargetKind(schedule) === 'agent' && schedule.agent_name === agentName,
            ),
        schedulesForWorkflow: (workflowName) =>
            schedules.filter(
                (schedule) => getScheduleTargetKind(schedule) === 'workflow' && schedule.workflow_name === workflowName,
            ),
        surfacesForAgent: (agentName) => surfaces.filter((surface) => surfaceReachesAgent(surface, agentName)),
        defaultSurfaces: surfaces.filter(surfaceUsesDefaultAgent),
    }), [
        schedules,
        surfaces,
        enableSchedules,
        enableSurfaces,
        schedulesQuery.isLoading,
        schedulesQuery.isError,
        surfacesQuery.isLoading,
        surfacesQuery.isError,
    ]);
}
