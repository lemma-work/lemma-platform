'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { CreateScheduleRequest, Schedule, ScheduleType, UpdateScheduleRequest } from '@/lib/types';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

export interface ScheduleFilters {
    scheduleType?: ScheduleType | null;
    isActive?: boolean | null;
    agentName?: string | null;
    workflowName?: string | null;
    limit?: number;
    pageToken?: string | null;
}

type ScheduleListResponse = {
    items?: Schedule[];
    next_page_token?: string | null;
    total?: number;
};

function schedulesQueryKey(podId: string | undefined, filters?: ScheduleFilters) {
    return [
        'schedules',
        podId,
        filters?.scheduleType ?? null,
        filters?.isActive ?? null,
        filters?.agentName ?? null,
        filters?.workflowName ?? null,
        filters?.limit ?? null,
        filters?.pageToken ?? null,
    ];
}

export function useSchedules(podId: string | undefined, filters: ScheduleFilters = {}) {
    return useQuery({
        queryKey: schedulesQueryKey(podId, filters),
        queryFn: async (): Promise<ScheduleListResponse> => {
            const response = await getLemmaClient(podId).schedules.list({
                scheduleType: filters.scheduleType,
                isActive: filters.isActive,
                agentName: filters.agentName,
                workflowName: filters.workflowName,
                limit: filters.limit ?? 100,
                pageToken: filters.pageToken ?? null,
            });

            return {
                items: response.items || [],
                next_page_token: response.next_page_token ?? null,
            };
        },
        enabled: !!podId,
        // Schedules are read from several places at once (schedules page, agents
        // list, agent/workflow detail). A short stale window lets them share one
        // cache entry instead of refetching on every mount.
        staleTime: 60_000,
    });
}

export function useCreateSchedule(podId: string | undefined) {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (payload: CreateScheduleRequest) => {
            return getLemmaClient(podId).schedules.create(payload);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['schedules', podId] });
        },
    });
}

export function useUpdateSchedule(podId: string | undefined) {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async ({ scheduleId, data }: { scheduleId: string; data: UpdateScheduleRequest }) => {
            return getLemmaClient(podId).schedules.update(scheduleId, data);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['schedules', podId] });
        },
    });
}

export function useDeleteSchedule(podId: string | undefined) {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (scheduleId: string) => {
            await getLemmaClient(podId).schedules.delete(scheduleId);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['schedules', podId] });
        },
    });
}
