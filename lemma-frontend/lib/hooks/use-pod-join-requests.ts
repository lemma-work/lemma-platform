'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from 'lemma-sdk';
import { getLemmaClient } from '../sdk/lemma-client';
import { OrganizationRole, PodRole } from '../types';

export type PodJoinRequestStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

export interface PodJoinRequest {
    approved_at?: string | null;
    approved_by_user_id?: string | null;
    created_at: string;
    id: string;
    org_role?: OrganizationRole | null;
    organization_id: string;
    pod_id: string;
    pod_role?: PodRole | null;
    requested_at: string;
    status: PodJoinRequestStatus;
    updated_at: string;
    user_id: string;
    user_email?: string | null;
    user_name?: string | null;
}

interface PodJoinRequestListResponse {
    items: PodJoinRequest[];
    next_page_token?: string | null;
}

interface ApproveJoinRequestInput {
    joinRequestId: string;
    orgRole?: OrganizationRole;
    podRole?: PodRole;
    organizationId?: string;
}

/**
 * A refusal is an answer, not a flake: this caller is not allowed to see join
 * requests, and asking three more times cannot change that. Without this the
 * client default (`retry: 3`) turns one refused request into four, and since a
 * failed query caches no data, `staleTime` never suppresses the burst — every
 * remount replays it.
 */
export function shouldRetryJoinRequestsFetch(failureCount: number, error: unknown): boolean {
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
}

/**
 * Listing join requests needs `pod.member.manage`. Callers that render the list
 * behind that permission must gate the *fetch* on it too, via `enabled` — a
 * permission check that only guards the JSX still lets every ordinary member
 * ask the server a question it will answer with 403.
 */
export const usePodJoinRequests = (
    podId: string,
    status: PodJoinRequestStatus = 'PENDING',
    options: { enabled?: boolean } = {}
) => {
    const { enabled = true } = options;

    return useQuery({
        queryKey: ['pods', podId, 'join-requests', status],
        queryFn: () =>
            getLemmaClient().request<PodJoinRequestListResponse>(
                'GET',
                `/pods/${encodeURIComponent(podId)}/join-requests`,
                {
                    params: {
                        status_filter: status,
                        limit: 100,
                    },
                }
            ),
        enabled: !!podId && enabled,
        retry: shouldRetryJoinRequestsFetch,
    });
};

export const useApprovePodJoinRequest = (podId: string) => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({
            joinRequestId,
            orgRole = OrganizationRole.ORG_MEMBER,
            podRole = PodRole.POD_USER,
        }: ApproveJoinRequestInput) =>
            getLemmaClient().request<PodJoinRequest>(
                'POST',
                `/pods/${encodeURIComponent(podId)}/join-requests/${encodeURIComponent(joinRequestId)}/approve`,
                {
                    body: {
                        org_role: orgRole,
                        pod_role: podRole,
                    },
                }
            ),
        onSuccess: (_data, variables) => {
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'join-requests'] });
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'members'] });

            if (variables.organizationId) {
                queryClient.invalidateQueries({
                    queryKey: ['organizations', variables.organizationId, 'members'],
                });
            }
        },
    });
};
