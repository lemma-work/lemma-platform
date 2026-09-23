export const POD_PERMISSION_IDS = [
    'pod.read',
    'pod.update',
    'pod.delete',
    'pod.member.manage',
    'pod.role.manage',
    'datastore.table.read',
    'datastore.table.create',
    'datastore.table.update',
    'datastore.table.delete',
    'datastore.record.read',
    'datastore.record.write',
    'folder.read',
    'folder.write',
    'folder.delete',
    'app.read',
    'app.create',
    'app.update',
    'app.delete',
    'app.publish',
    'agent.read',
    'agent.create',
    'agent.update',
    'agent.delete',
    'agent.execute',
    'function.read',
    'function.create',
    'function.update',
    'function.delete',
    'function.execute',
    'workflow.read',
    'workflow.create',
    'workflow.update',
    'workflow.delete',
    'workflow.execute',
    'schedule.read',
    'schedule.create',
    'schedule.update',
    'schedule.delete',
    'conversation.read',
    'conversation.write',
    'connector.use',
    'connector_account.use',
    'connector_account.manage',
] as const;

export type PodPermissionId = (typeof POD_PERMISSION_IDS)[number];
export type PodAccessMode = 'operator' | 'builder' | 'admin';

export const ADMIN_PERMISSIONS: PodPermissionId[] = [
    'pod.delete',
    'pod.member.manage',
    'pod.role.manage',
];

export const BUILDER_PERMISSIONS: PodPermissionId[] = [
    'pod.update',
    'datastore.table.create',
    'datastore.table.update',
    'datastore.table.delete',
    'folder.write',
    'folder.delete',
    'app.create',
    'app.update',
    'app.delete',
    'app.publish',
    'agent.create',
    'agent.update',
    'agent.delete',
    'function.create',
    'function.update',
    'function.delete',
    'workflow.create',
    'workflow.update',
    'workflow.delete',
    'schedule.create',
    'schedule.update',
    'schedule.delete',
    'connector_account.manage',
];

export const POD_ROUTE_POLICIES = {
    home: ['pod.read'],
    data: ['datastore.table.read', 'datastore.record.read', 'folder.read'],
    files: ['folder.read'],
    agents: ['agent.read', 'agent.execute', 'agent.create'],
    functions: ['function.read', 'function.execute', 'function.create'],
    workflows: ['workflow.read', 'workflow.execute', 'workflow.create'],
    schedules: ['schedule.read', 'schedule.create'],
    connectors: ['connector.use', 'connector_account.use', 'connector_account.manage'],
    apps: ['app.read', 'app.create'],
    surfaces: ['agent.update', 'connector_account.manage'],
    conversations: ['conversation.read', 'conversation.write'],
    settings: ['pod.member.manage', 'pod.role.manage', 'pod.update'],
} as const satisfies Record<string, readonly PodPermissionId[]>;

export type PodRoutePolicyKey = keyof typeof POD_ROUTE_POLICIES;

export function toPermissionSet(actions: string[] | null | undefined): Set<string> {
    return new Set((actions || []).filter(Boolean));
}

export function hasPermission(actions: Iterable<string>, permission: PodPermissionId): boolean {
    return new Set(actions).has(permission);
}

export function hasAnyPermission(actions: Iterable<string>, permissions: readonly PodPermissionId[]): boolean {
    const actionSet = new Set(actions);
    return permissions.some((permission) => actionSet.has(permission));
}

export function hasAllPermissions(actions: Iterable<string>, permissions: readonly PodPermissionId[]): boolean {
    const actionSet = new Set(actions);
    return permissions.every((permission) => actionSet.has(permission));
}

export function getPodAccessMode(actions: string[] | Set<string> | null | undefined): PodAccessMode {
    const actionSet = actions instanceof Set ? actions : toPermissionSet(actions);
    if (hasAnyPermission(actionSet, ADMIN_PERMISSIONS)) return 'admin';
    if (hasAnyPermission(actionSet, BUILDER_PERMISSIONS)) return 'builder';
    return 'operator';
}

export function canAccessPodRoute(
    actions: string[] | Set<string> | null | undefined,
    route: PodRoutePolicyKey,
): boolean {
    return hasAnyPermission(actions instanceof Set ? actions : toPermissionSet(actions), POD_ROUTE_POLICIES[route]);
}
