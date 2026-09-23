import type { PodPermissionId } from '@/lib/authz/pod-permissions';

export type ResourceWithAllowedActions = {
    allowed_actions?: string[] | null;
};

export function resourceAllows(
    resource: ResourceWithAllowedActions | null | undefined,
    permission: PodPermissionId,
    fallback = false,
): boolean {
    const actions = resource?.allowed_actions;
    if (!Array.isArray(actions)) return fallback;
    return actions.includes(permission);
}

export function resourceAllowsAny(
    resource: ResourceWithAllowedActions | null | undefined,
    permissions: readonly PodPermissionId[],
    fallback = false,
): boolean {
    const actions = resource?.allowed_actions;
    if (!Array.isArray(actions)) return fallback;
    return permissions.some((permission) => actions.includes(permission));
}
