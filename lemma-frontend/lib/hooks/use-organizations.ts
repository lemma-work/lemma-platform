'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '../sdk/lemma-client';
import {
    OrganizationInvitationStatus,
    type OrganizationJoinPolicy,
    type OrganizationRole,
    type PaginatedResponse,
    type Organization,
    type OrganizationMember,
    type OrganizationInvitation,
    type ApiMessageResponse,
    type OrganizationSlugAvailability,
} from '../types';

export const useOrganizations = (options?: { enabled?: boolean }) => {
    return useQuery({
        queryKey: ['organizations'],
        queryFn: () => getLemmaClient().organizations.list() as Promise<PaginatedResponse<Organization>>,
        enabled: options?.enabled ?? true,
    });
};

export const useOrganizationDetails = (orgId: string) => {
    return useQuery({
        queryKey: ['organizations', orgId],
        queryFn: () => getLemmaClient().organizations.get(orgId) as Promise<Organization>,
        enabled: !!orgId,
    });
};

export const useSuggestedOrganizations = (options?: { enabled?: boolean }) => {
    return useQuery({
        queryKey: ['organizations', 'suggested'],
        queryFn: () =>
            getLemmaClient().request<PaginatedResponse<Organization>>(
                'GET',
                '/organizations/suggested',
                { params: { limit: 100 } }
            ),
        enabled: options?.enabled ?? true,
    });
};

/**
 * Whether an organization can be created under this slug — and, when a
 * candidate name is passed, under that name too. Names are globally unique, so
 * a slug-only check still leaves a 409 waiting on create.
 */
export const useOrganizationNameAvailability = (
    { slug, name }: { slug: string; name?: string },
    options?: { enabled?: boolean }
) => {
    const normalizedSlug = slug.trim().toLowerCase();
    const normalizedName = name?.trim() || '';

    const query = useQuery({
        queryKey: ['organizations', 'slug-availability', normalizedSlug, normalizedName],
        queryFn: () =>
            getLemmaClient().request<OrganizationSlugAvailability>(
                'GET',
                '/organizations/slug-availability',
                {
                    params: normalizedName
                        ? { slug: normalizedSlug, name: normalizedName }
                        : { slug: normalizedSlug },
                }
            ),
        enabled: Boolean(normalizedSlug) && (options?.enabled ?? true),
    });

    // Undefined while the probe is in flight — callers must not read "not yet
    // answered" as "taken".
    const available = query.data
        ? query.data.available && query.data.name_available !== false
        : undefined;

    return { ...query, available };
};

export const useCreateOrganization = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: {
            name: string;
            join_policy?: OrganizationJoinPolicy;
            email_domain?: string | null;
            /** Take the next free name rather than conflicting. For a derived
             *  name the user never typed; leave unset for one they did. */
            resolve_name_conflicts?: boolean;
        }) => getLemmaClient().organizations.create(data) as Promise<Organization>,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['organizations'] });
            queryClient.invalidateQueries({ queryKey: ['organizations', 'suggested'] });
        },
    });
};

export const useUpdateOrganization = (orgId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: { name?: string; email_domain?: string | null; join_policy?: OrganizationJoinPolicy }) =>
            getLemmaClient().request<Organization>(
                'PATCH',
                `/organizations/${encodeURIComponent(orgId)}`,
                { body: data }
            ),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['organizations'] });
            queryClient.invalidateQueries({ queryKey: ['organizations', orgId] });
            queryClient.invalidateQueries({ queryKey: ['organizations', 'suggested'] });
        },
    });
};

export const useJoinSuggestedOrganization = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (organizationId: string) =>
            getLemmaClient().request<Organization>(
                'POST',
                `/organizations/${encodeURIComponent(organizationId)}/join`
            ),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['organizations'] });
            queryClient.invalidateQueries({ queryKey: ['organizations', 'suggested'] });
            queryClient.invalidateQueries({ queryKey: ['pods'] });
        },
    });
};

export const useOrganizationMembers = (orgId: string) => {
    return useQuery({
        queryKey: ['organizations', orgId, 'members'],
        queryFn: () => getLemmaClient().organizations.members.list(orgId) as Promise<PaginatedResponse<OrganizationMember>>,
        enabled: !!orgId,
    });
};

export const useOrganizationInvitations = (orgId: string) => {
    return useQuery({
        queryKey: ['organizations', orgId, 'invitations'],
        queryFn: () => getLemmaClient().organizations.invitations.list(orgId) as Promise<PaginatedResponse<OrganizationInvitation>>,
        enabled: !!orgId,
    });
};

export const useMyOrganizationInvitations = (
    status: OrganizationInvitationStatus = OrganizationInvitationStatus.PENDING,
    options?: { enabled?: boolean }
) => {
    return useQuery({
        queryKey: ['organizations', 'invitations', 'mine', status],
        queryFn: () => getLemmaClient().organizations.invitations.listMine({ status }) as Promise<PaginatedResponse<OrganizationInvitation>>,
        enabled: options?.enabled ?? true,
    });
};

export const useOrganizationInvitation = (invitationId: string) => {
    return useQuery({
        queryKey: ['organizations', 'invitations', invitationId],
        queryFn: () => getLemmaClient().organizations.invitations.get(invitationId) as Promise<OrganizationInvitation>,
        enabled: !!invitationId,
    });
};

export const useInviteMember = (orgId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: { email: string; role: OrganizationRole; pod_id?: string; pod_role?: string; redirect_uri?: string }) =>
            getLemmaClient().organizations.invitations.invite(orgId, {
                email: data.email,
                role: data.role,
                pod_id: data.pod_id,
                pod_role: data.pod_role,
                redirect_uri: data.redirect_uri,
            }) as Promise<OrganizationInvitation>,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['organizations', orgId, 'invitations'] });
        },
    });
};

export const useRevokeInvitation = (orgId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (invitationId: string) => {
            void orgId;
            await getLemmaClient().organizations.invitations.revoke(invitationId);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['organizations', orgId, 'invitations'] });
        },
    });
};

export const useAcceptOrganizationInvitation = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (invitationId: string) => getLemmaClient().organizations.invitations.accept(invitationId) as Promise<ApiMessageResponse>,
        onSuccess: (_data, invitationId) => {
            queryClient.invalidateQueries({ queryKey: ['organizations'] });
            queryClient.invalidateQueries({ queryKey: ['organizations', 'invitations', 'mine'] });
            queryClient.invalidateQueries({ queryKey: ['organizations', 'invitations', invitationId] });
        },
    });
};

export const useRejectOrganizationInvitation = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (invitationId: string) => {
            await getLemmaClient().organizations.invitations.revoke(invitationId);
        },
        onSuccess: (_data, invitationId) => {
            queryClient.invalidateQueries({ queryKey: ['organizations', 'invitations', 'mine'] });
            queryClient.invalidateQueries({ queryKey: ['organizations', 'invitations', invitationId] });
        },
    });
};

export const useRemoveOrgMember = (orgId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (memberId: string) => {
            await getLemmaClient().organizations.members.remove(orgId, memberId);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['organizations', orgId, 'members'] });
        },
    });
};

export const useUpdateOrgMemberRole = (orgId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: { memberId: string; role: OrganizationRole }) =>
            getLemmaClient().organizations.members.updateRole(orgId, data.memberId, data.role) as Promise<OrganizationMember>,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['organizations', orgId, 'members'] });
        },
    });
};
