'use client';

import type { MyUsageLimitsResponse } from 'lemma-sdk';
import { useQuery } from '@tanstack/react-query';

import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { RecentUsage, UsageLimits, UsageStats, UsageSummary } from '@/lib/types';

export interface UsageFilters {
    start?: string | null;
    end?: string | null;
    modelName?: string | null;
    podId?: string | null;
    userId?: string | null;
    agentId?: string | null;
    agentRunId?: string | null;
    conversationId?: string | null;
    usageKind?: string | null;
    status?: string | null;
    days?: number;
    limit?: number;
}

export interface UsageStatsFilters extends UsageFilters {
    granularity?: 'hour' | 'day' | 'week';
    groupBy?: string | null;
}

function compactParams(params: Record<string, string | number | boolean | null | undefined>) {
    return Object.fromEntries(
        Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== '')
    );
}

function usageParams(filters: UsageFilters = {}) {
    return compactParams({
        start: filters.start,
        end: filters.end,
        model_name: filters.modelName,
        pod_id: filters.podId,
        user_id: filters.userId,
        agent_id: filters.agentId,
        agent_run_id: filters.agentRunId,
        conversation_id: filters.conversationId,
        usage_kind: filters.usageKind,
        status: filters.status,
        days: filters.days,
        limit: filters.limit,
    });
}

function encodePath(value: string) {
    return encodeURIComponent(value);
}

export function useUsageSummary(
    organizationId: string | undefined,
    filters: UsageFilters = {},
    options?: { enabled?: boolean; self?: boolean }
) {
    return useQuery({
        queryKey: ['usage', 'summary', options?.self ? 'self' : 'organization', organizationId, filters],
        queryFn: () => {
            if (!organizationId && !options?.self) {
                throw new Error('Organization is required to load usage.');
            }

            return getLemmaClient().request<UsageSummary>(
                'GET',
                options?.self ? `/usage/me/summary` : `/usage/organizations/${encodePath(organizationId!)}/summary`,
                { params: { ...usageParams(filters), ...(options?.self ? { organization_id: organizationId } : {}) } }
            );
        },
        enabled: (Boolean(organizationId) || Boolean(options?.self)) && (options?.enabled ?? true),
    });
}

export function useUsageStats(
    organizationId: string | undefined,
    filters: UsageStatsFilters = {},
    options?: { enabled?: boolean; self?: boolean }
) {
    return useQuery({
        queryKey: ['usage', 'stats', options?.self ? 'self' : 'organization', organizationId, filters],
        queryFn: () => {
            if (!organizationId && !options?.self) {
                throw new Error('Organization is required to load usage stats.');
            }

            return getLemmaClient().request<UsageStats>(
                'GET',
                options?.self ? `/usage/me/stats` : `/usage/organizations/${encodePath(organizationId!)}/stats`,
                {
                    params: {
                        ...usageParams(filters),
                        ...(options?.self ? { organization_id: organizationId } : {}),
                        granularity: filters.granularity ?? 'day',
                        group_by: filters.groupBy,
                    },
                }
            );
        },
        enabled: (Boolean(organizationId) || Boolean(options?.self)) && (options?.enabled ?? true),
    });
}

export function useRecentUsage(
    organizationId: string | undefined,
    filters: UsageFilters = {},
    options?: { enabled?: boolean; self?: boolean }
) {
    return useQuery({
        queryKey: ['usage', 'recent', options?.self ? 'self' : 'organization', organizationId, filters],
        queryFn: () => {
            if (!organizationId && !options?.self) {
                throw new Error('Organization is required to load recent usage.');
            }

            return getLemmaClient().request<RecentUsage>(
                'GET',
                options?.self ? `/usage/me/events` : `/usage/organizations/${encodePath(organizationId!)}/events`,
                { params: { ...usageParams(filters), ...(options?.self ? { organization_id: organizationId } : {}) } }
            );
        },
        enabled: (Boolean(organizationId) || Boolean(options?.self)) && (options?.enabled ?? true),
    });
}

export function useUsageLimits(organizationId: string | undefined, options?: { enabled?: boolean; self?: boolean }) {
    return useQuery({
        queryKey: ['usage', 'limits', organizationId],
        queryFn: () => {
            if (!organizationId) {
                throw new Error('Organization is required to load usage limits.');
            }

            return getLemmaClient().request<UsageLimits>(
                'GET',
                `/usage/organizations/${encodePath(organizationId)}/limits`
            );
        },
        enabled: (Boolean(organizationId) || Boolean(options?.self)) && (options?.enabled ?? true),
    });
}

export function useMyUsageLimits(organizationId?: string, options?: { enabled?: boolean; poll?: boolean }) {
    return useQuery({
        queryKey: ['usage', 'my-limits', organizationId],
        queryFn: () => getLemmaClient().request<MyUsageLimitsResponse>('GET', '/usage/me/limits', {
            params: { organization_id: organizationId },
        }),
        enabled: options?.enabled ?? true,
        staleTime: 0,
        refetchInterval: options?.poll ? 30_000 : false,
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
    });
}
