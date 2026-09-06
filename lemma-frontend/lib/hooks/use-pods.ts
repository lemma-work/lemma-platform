'use client';

import { ApiError } from 'lemma-sdk';
import { useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '../sdk/lemma-client';
import { navigationQueryKey, organizationHomeQueryKey } from './pod-query-keys';
import type { CreatePodData, UpdatePodData, Pod, PaginatedResponse } from '../types';

export const usePods = (orgId?: string, options?: { enabled?: boolean }) => {
    const enabled = options?.enabled ?? true;

    return useQuery({
        queryKey: ['pods', orgId],
        queryFn: () =>
            getLemmaClient().pods.listByOrganization(orgId!) as Promise<PaginatedResponse<Pod>>,
        enabled: enabled && !!orgId,
    });
};

/**
 * An organization as navigation knows it: enough to label and to link to.
 *
 * Narrower than the full organization record on purpose. `/organizations/navigation`
 * answers for every organization at once, so it returns identity only — the
 * fuller record is still one `organizations.get(id)` away for screens that
 * need it.
 */
export type NavigationOrg = {
    id: string;
    name: string;
    slug?: string | null;
    role: string;
};

/** A pod as navigation knows it, plus which organization it came from. */
export type AccessiblePod = {
    id: string;
    name: string;
    description?: string | null;
    icon_url?: string | null;
    updated_at: string;
    organization_id: string;
    organization: NavigationOrg;
    organization_name: string;
};

export type AccessiblePodGroup = {
    organization: NavigationOrg;
    pods: AccessiblePod[];
};

/**
 * Every pod the user can reach, across every organization, in one request.
 *
 * This used to fetch the organization list and then a pod list per
 * organization, so a user in five organizations waited out six sequential
 * round trips before the sidebar could draw. `/organizations/navigation`
 * answers all of it at once and costs the backend two queries regardless of
 * how many organizations there are.
 *
 * The payload is deliberately shallow — ids, names, icons. Anything richer
 * (apps, agents, per-pod roles) belongs to `/organizations/{id}/home`, which
 * `useOrganizationHome` fetches for the one organization actually on screen.
 */
export const useAccessiblePods = (options?: { enabled?: boolean }) => {
    const enabled = options?.enabled ?? true;

    const navigationQuery = useQuery({
        queryKey: navigationQueryKey(),
        queryFn: () => getLemmaClient().organizations.navigation(),
        enabled,
    });

    const groups = useMemo<AccessiblePodGroup[]>(() => {
        return (navigationQuery.data?.items || []).map((entry) => {
            const organization: NavigationOrg = {
                id: entry.id,
                name: entry.name,
                slug: entry.slug,
                role: entry.role,
            };
            return {
                organization,
                pods: (entry.pods || []).map((pod) => ({
                    id: pod.id,
                    name: pod.name,
                    description: pod.description,
                    icon_url: pod.icon_url,
                    updated_at: pod.updated_at,
                    // Carried down from the grouping rather than returned per
                    // pod: the endpoint already nests pods under their
                    // organization, so repeating it on every pod would be
                    // payload for nothing.
                    organization_id: entry.id,
                    organization,
                    organization_name: entry.name,
                })),
            };
        });
    }, [navigationQuery.data?.items]);

    const organizations = useMemo(() => groups.map((group) => group.organization), [groups]);
    const pods = useMemo(() => groups.flatMap((group) => group.pods), [groups]);

    return {
        data: {
            items: pods,
            groups,
            organizations,
            hasMultipleOrganizations: organizations.length > 1,
        },
        isLoading: navigationQuery.isLoading,
        isError: navigationQuery.isError,
        refetch: navigationQuery.refetch,
        error: navigationQuery.error,
    };
};

/**
 * One organization's pods with their apps, agents and the caller's roles.
 *
 * The detail half of the split: fetched for the organization on screen rather
 * than for every organization a person belongs to, because that payload grows
 * with content and most of it would never be looked at. Cached server-side for
 * thirty seconds, so revisiting a landing page is a cache read.
 */
export const useOrganizationHome = (orgId?: string, options?: { enabled?: boolean }) => {
    const enabled = options?.enabled ?? true;

    return useQuery({
        queryKey: organizationHomeQueryKey(orgId),
        queryFn: () => getLemmaClient().organizations.home(orgId!),
        enabled: enabled && !!orgId,
    });
};

export const usePod = (id: string | undefined) => {
    return useQuery({
        queryKey: ['pods', id],
        queryFn: () => getLemmaClient().pods.get(id!) as Promise<Pod>,
        enabled: !!id && id !== 'undefined',
        retry: (failureCount, error) => {
            if (error instanceof ApiError) {
                if (
                    error.statusCode === 401 ||
                    error.statusCode === 403 ||
                    error.statusCode === 404 ||
                    error.code === 'INSUFFICIENT_ROLE'
                ) {
                    return false;
                }
            }

            return failureCount < 2;
        },
    });
};

export const useCreatePod = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (data: CreatePodData) => getLemmaClient().pods.create(data) as Promise<Pod>,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pods'] });
        },
    });
};

export const useUpdatePod = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ id, data }: { id: string; data: UpdatePodData }) =>
            getLemmaClient().pods.update(id, data) as Promise<Pod>,
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['pods'] });
            queryClient.invalidateQueries({ queryKey: ['pods', variables.id] });
        },
    });
};

export const useDeletePod = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (id: string) => {
            await getLemmaClient().pods.delete(id);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pods'] });
        },
    });
};
