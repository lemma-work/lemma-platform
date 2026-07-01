'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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

export const usePodJoinRequests = (podId: string, status: PodJoinRequestStatus = 'PENDING') => {
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
        enabled: !!podId,
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
