'use client';

import { useQuery } from '@tanstack/react-query';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import {
    canAccessPodRoute,
    getPodAccessMode,
    hasAllPermissions,
    hasAnyPermission,
    hasPermission,
    toPermissionSet,
    type PodPermissionId,
    type PodRoutePolicyKey,
} from '@/lib/authz/pod-permissions';

type PodPermissionsResponse = {
    pod_id: string;
    actions: string[];
};

export async function fetchPodPermissions(podId: string): Promise<PodPermissionsResponse> {
    return getLemmaClient(podId).podPermissions.me();
}

export function usePodAccess(podId: string | undefined) {
    const query = useQuery({
        queryKey: ['pods', podId, 'permissions', 'me'],
        queryFn: () => fetchPodPermissions(podId!),
        enabled: Boolean(podId),
        staleTime: 60 * 1000,
    });

    const actions = query.data?.actions || [];
    const actionSet = toPermissionSet(actions);
    const accessMode = getPodAccessMode(actionSet);
    const isBuilder = accessMode === 'builder' || accessMode === 'admin';

    return {
        ...query,
        isLoading: query.isLoading,
        actions,
        actionSet,
        accessMode,
        isBuilder,
        can: (permission: PodPermissionId) => hasPermission(actionSet, permission),
        canAny: (permissions: readonly PodPermissionId[]) => hasAnyPermission(actionSet, permissions),
        canAll: (permissions: readonly PodPermissionId[]) => hasAllPermissions(actionSet, permissions),
        canAccessRoute: (route: PodRoutePolicyKey) => canAccessPodRoute(actionSet, route),
    };
}
