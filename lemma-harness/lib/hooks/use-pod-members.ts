'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '../sdk/lemma-client';
import type {
    PodPermissionResponse,
    PodRolePermissionsReplaceRequest,
    PodRolePermissionsResponse,
    PodRoleResponse,
} from 'lemma-sdk';
import type { PodRole } from '../types';

export const usePodMembers = (podId: string) => {
    return useQuery({
        queryKey: ['pods', podId, 'members'],
        queryFn: () => getLemmaClient().podMembers.list(podId),
        enabled: !!podId,
    });
};

export const useAddPodMember = (podId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: { organization_member_id: string; role: PodRole }) =>
            getLemmaClient().podMembers.add(podId, {
                organization_member_id: data.organization_member_id,
                roles: [data.role],
            }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'members'] });
        },
    });
};

export const useRemovePodMember = (podId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (memberId: string) => {
            await getLemmaClient().podMembers.remove(podId, memberId);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'members'] });
        },
    });
};

export const useUpdatePodMemberRole = (podId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: { memberId: string; role: PodRole }) =>
            getLemmaClient().podMembers.updateRole(podId, data.memberId, data.role),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'members'] });
        },
    });
};

export const useUpdatePodMemberRoles = (podId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: { memberId: string; roles: string[] }) =>
            getLemmaClient().podMembers.updateRoles(podId, data.memberId, data.roles),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'members'] });
        },
    });
};

export const usePodRoles = (podId: string) => {
    return useQuery({
        queryKey: ['pods', podId, 'roles'],
        queryFn: () => getLemmaClient(podId).podRoles.list() as Promise<{ items: PodRoleResponse[] }>,
        enabled: !!podId,
    });
};

export const usePodPermissionCatalog = (podId: string) => {
    return useQuery({
        queryKey: ['pods', podId, 'permissions', 'catalog'],
        queryFn: () => getLemmaClient(podId).podPermissions.catalog() as Promise<{ items: PodPermissionResponse[] }>,
        enabled: !!podId,
    });
};

export const usePodRolePermissions = (podId: string, roleName?: string | null) => {
    return useQuery({
        queryKey: ['pods', podId, 'roles', roleName, 'permissions'],
        queryFn: () => getLemmaClient(podId).podRoles.permissions.get(roleName as string) as Promise<PodRolePermissionsResponse>,
        enabled: !!podId && !!roleName,
    });
};

export const useCreatePodRole = (podId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: { name: string; description?: string | null; permission_ids?: string[] }) =>
            getLemmaClient(podId).podRoles.create(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'roles'] });
        },
    });
};

export const useUpdatePodRole = (podId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: { roleName: string; name: string; description?: string | null; permission_ids?: string[] }) =>
            getLemmaClient(podId).podRoles.update(data.roleName, {
                name: data.name,
                description: data.description,
                permission_ids: data.permission_ids,
            }),
        onSuccess: (_data, variables) => {
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'roles'] });
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'roles', variables.roleName, 'permissions'] });
        },
    });
};

export const useDeletePodRole = (podId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (roleName: string) => getLemmaClient(podId).podRoles.delete(roleName),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'roles'] });
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'members'] });
        },
    });
};

export const useReplacePodRolePermissions = (podId: string) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (data: { roleName: string; permissions: { grants?: Array<{ resource_type: string; resource_name: string; permission_ids?: string[] }> } }) =>
            getLemmaClient(podId).podRoles.permissions.replace(data.roleName, data.permissions as PodRolePermissionsReplaceRequest),
        onSuccess: (_data, variables) => {
            queryClient.invalidateQueries({ queryKey: ['pods', podId, 'roles', variables.roleName, 'permissions'] });
        },
    });
};
