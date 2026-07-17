'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Copy, Globe2, LockKeyhole, Share2, Trash2, UserRound, UsersRound, type LemmaIcon } from '@/components/ui/icons';
import type { PodMemberResponse, ResourceAccessGrantResponse, ResourceAccessResponse } from 'lemma-sdk';

import { ConceptHint } from '@/components/education/concept-hint';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { cn } from '@/lib/utils';

export type ResourceVisibilityValue = 'PERSONAL' | 'POD' | 'RESTRICTED' | 'PUBLIC';
export type ShareableResourceType =
    | 'agent'
    | 'function'
    | 'workflow'
    | 'schedule'
    | 'datastore_table'
    | 'document'
    | 'folder'
    | 'app';

type ResourceVisibilityCopy = {
    value: ResourceVisibilityValue;
    label: string;
    shortDescription: string;
    description: string;
    icon: LemmaIcon;
    className: string;
};

const VISIBILITY_VALUES: ResourceVisibilityValue[] = ['PERSONAL', 'POD', 'RESTRICTED', 'PUBLIC'];
const NO_GRANTEE_VALUE = '__none__';

type AccessLevel = {
    value: string;
    label: string;
    permissionIds: string[];
};

const ACCESS_LEVELS_BY_RESOURCE: Record<ShareableResourceType, AccessLevel[]> = {
    agent: [
        { value: 'viewer', label: 'Viewer', permissionIds: ['agent.read'] },
        { value: 'runner', label: 'Runner', permissionIds: ['agent.read', 'agent.execute'] },
        { value: 'editor', label: 'Editor', permissionIds: ['agent.read', 'agent.execute', 'agent.update'] },
    ],
    function: [
        { value: 'viewer', label: 'Viewer', permissionIds: ['function.read'] },
        { value: 'runner', label: 'Runner', permissionIds: ['function.read', 'function.execute'] },
        { value: 'editor', label: 'Editor', permissionIds: ['function.read', 'function.execute', 'function.update'] },
    ],
    workflow: [
        { value: 'viewer', label: 'Viewer', permissionIds: ['workflow.read'] },
        { value: 'runner', label: 'Runner', permissionIds: ['workflow.read', 'workflow.execute'] },
        { value: 'editor', label: 'Editor', permissionIds: ['workflow.read', 'workflow.execute', 'workflow.update'] },
    ],
    schedule: [
        { value: 'viewer', label: 'Viewer', permissionIds: ['schedule.read'] },
        { value: 'editor', label: 'Editor', permissionIds: ['schedule.read', 'schedule.update'] },
    ],
    datastore_table: [
        { value: 'viewer', label: 'Viewer', permissionIds: ['datastore.table.read', 'datastore.record.read'] },
        { value: 'editor', label: 'Editor', permissionIds: ['datastore.table.read', 'datastore.record.read', 'datastore.record.write', 'datastore.table.update'] },
    ],
    document: [
        { value: 'viewer', label: 'Viewer', permissionIds: ['folder.read'] },
        { value: 'editor', label: 'Editor', permissionIds: ['folder.read', 'folder.write'] },
    ],
    folder: [
        { value: 'viewer', label: 'Viewer', permissionIds: ['folder.read'] },
        { value: 'editor', label: 'Editor', permissionIds: ['folder.read', 'folder.write'] },
    ],
    app: [
        { value: 'viewer', label: 'Viewer', permissionIds: ['app.read'] },
        { value: 'editor', label: 'Editor', permissionIds: ['app.read', 'app.update'] },
    ],
};

function toResourceLabel(resourceLabel?: string) {
    return resourceLabel?.trim() || 'resources';
}

export function normalizeResourceVisibility(value?: string | null): ResourceVisibilityValue {
    const normalized = String(value || 'POD').trim().toUpperCase();
    if (normalized === 'PRIVATE' || normalized === 'OWNER' || normalized === 'USER') return 'PERSONAL';
    if (VISIBILITY_VALUES.includes(normalized as ResourceVisibilityValue)) {
        return normalized as ResourceVisibilityValue;
    }
    return 'POD';
}

export function getResourceVisibilityCopy(
    value?: string | null,
    resourceLabel?: string,
): ResourceVisibilityCopy {
    const visibility = normalizeResourceVisibility(value);
    const resources = toResourceLabel(resourceLabel);

    if (visibility === 'PERSONAL') {
        return {
            value: visibility,
            label: 'Only me',
            shortDescription: 'Private to you',
            description: 'Only you can open and use it.',
            icon: UserRound,
            className: 'border-[color:var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]',
        };
    }

    if (visibility === 'RESTRICTED') {
        return {
            value: visibility,
            label: 'Specific access',
            shortDescription: 'Choose people',
            description: 'Only people with access can open it.',
            icon: LockKeyhole,
            className: 'state-badge-warning',
        };
    }

    if (visibility === 'PUBLIC') {
        return {
            value: visibility,
            label: 'Anyone with link',
            shortDescription: 'Link access',
            description: 'Anyone with the link can open it.',
            icon: Globe2,
            className: 'state-badge-info',
        };
    }

    return {
        value: 'POD',
        label: 'Pod workspace',
        shortDescription: `Viewable by ${resources} readers`,
        description: `Everyone with permission to view ${resources} in this pod can open it.`,
        icon: UsersRound,
        className: 'state-badge-brand',
    };
}

function formatRoleLabel(value?: string | null) {
    return String(value || 'Role')
        .toLowerCase()
        .split('_')
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

function normalizePermissionSet(permissionIds: string[]) {
    return new Set(permissionIds.slice().sort());
}

function samePermissions(left: string[], right: string[]) {
    const leftSet = normalizePermissionSet(left);
    const rightSet = normalizePermissionSet(right);
    if (leftSet.size !== rightSet.size) return false;
    return [...leftSet].every((permission) => rightSet.has(permission));
}

function grantKey(grant: Pick<ResourceAccessGrantResponse, 'grantee_type' | 'grantee_id'>) {
    return `${grant.grantee_type}:${grant.grantee_id}`;
}

function sameGrantLists(left: ResourceAccessGrantResponse[], right: ResourceAccessGrantResponse[]) {
    if (left.length !== right.length) return false;
    const rightByKey = new Map(right.map((grant) => [grantKey(grant), grant]));

    return left.every((leftGrant) => {
        const rightGrant = rightByKey.get(grantKey(leftGrant));
        if (!rightGrant) return false;
        return samePermissions(leftGrant.permission_ids || [], rightGrant.permission_ids || []);
    });
}

function getAccessLabel(resourceType: ShareableResourceType, permissionIds: string[]) {
    const levels = ACCESS_LEVELS_BY_RESOURCE[resourceType] || [];
    const exact = levels.find((level) => samePermissions(level.permissionIds, permissionIds));
    if (exact) return exact.label;
    const strongest = levels
        .slice()
        .reverse()
        .find((level) => level.permissionIds.every((permission) => permissionIds.includes(permission)));
    return strongest ? strongest.label : `${permissionIds.length} permission${permissionIds.length === 1 ? '' : 's'}`;
}

function getGrantLabel(grant: ResourceAccessGrantResponse) {
    if (grant.grantee_type === 'ROLE') return formatRoleLabel(grant.role_name);
    return grant.display_name || grant.email || 'Pod member';
}

function getGrantInitials(grant: ResourceAccessGrantResponse) {
    const label = getGrantLabel(grant);
    const parts = label.split(/\s+/).filter(Boolean);
    const initials = parts.slice(0, 2).map((part) => part[0]?.toUpperCase()).join('');
    return initials || <UserRound className="h-4 w-4" />;
}

const VISIBILITY_TONE: Record<string, string> = {
    PERSONAL: 'text-[var(--text-secondary)]',
    RESTRICTED: 'text-[var(--state-warning)]',
    PUBLIC: 'text-[var(--state-info)]',
    POD: 'text-[var(--text-tertiary)]',
};

export function ResourceVisibilityBadge({
    visibility,
    resourceLabel,
    className,
    compact = false,
    hideWhenDefault,
}: {
    visibility?: string | null;
    resourceLabel?: string;
    className?: string;
    compact?: boolean;
    /** When the value is the default (pod workspace), render nothing. Defaults to `compact` — dense list rows hide the common case so only exceptions stand out. */
    hideWhenDefault?: boolean;
}) {
    const copy = getResourceVisibilityCopy(visibility, resourceLabel);
    const Icon = copy.icon;
    const tone = VISIBILITY_TONE[copy.value] ?? 'text-[var(--text-tertiary)]';
    const shouldHideDefault = hideWhenDefault ?? compact;

    if (shouldHideDefault && copy.value === 'POD') {
        return null;
    }

    const trigger = compact ? (
        <span className={cn('inline-flex shrink-0 items-center justify-center', tone, className)}>
            <Icon className="h-4 w-4 shrink-0" />
            <span className="sr-only">{copy.label}</span>
        </span>
    ) : (
        <Badge
            className={cn(
                'h-6 max-w-full gap-1.5 truncate border-0 bg-[color:color-mix(in_srgb,var(--surface-2)_55%,transparent)] text-xs font-medium',
                tone,
                className,
            )}
        >
            <Icon className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{copy.label}</span>
        </Badge>
    );

    return (
        <TooltipProvider>
            <Tooltip>
                <TooltipTrigger asChild>{trigger}</TooltipTrigger>
                <TooltipContent className="max-w-xs">
                    {copy.description}
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}

export function ResourceShareButton({
    value,
    onChange,
    podId,
    resourceType,
    resourceId,
    resourceLabel,
    resourceName,
    shareUrl,
    className,
    buttonClassName,
    disabled = false,
    options = VISIBILITY_VALUES,
    trigger,
}: {
    value?: string | null;
    onChange: (value: ResourceVisibilityValue) => void | Promise<void>;
    podId?: string | null;
    resourceType?: ShareableResourceType | null;
    resourceId?: string | null;
    resourceLabel?: string;
    resourceName?: string | null;
    shareUrl?: string | null;
    className?: string;
    buttonClassName?: string;
    disabled?: boolean;
    options?: ResourceVisibilityValue[];
    trigger?: (props: { openShare: () => void; disabled: boolean }) => ReactNode;
}) {
    const current = normalizeResourceVisibility(value);
    const queryClient = useQueryClient();
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState(false);
    const [draftVisibility, setDraftVisibility] = useState<ResourceVisibilityValue>(current);
    const [selectedGrantee, setSelectedGrantee] = useState<string>(NO_GRANTEE_VALUE);
    const [selectedAccessLevel, setSelectedAccessLevel] = useState<string>('viewer');
    const [draftGrants, setDraftGrants] = useState<ResourceAccessGrantResponse[]>([]);
    const [saveError, setSaveError] = useState<string | null>(null);
    const draftCopy = getResourceVisibilityCopy(draftVisibility, resourceLabel);
    const DraftIcon = draftCopy.icon;
    const hasVisibilityChange = draftVisibility !== current;
    const canManageSpecificAccess = Boolean(podId && resourceType && resourceId);
    const accessLevels = resourceType ? ACCESS_LEVELS_BY_RESOURCE[resourceType] : [];
    const selectedAccess = accessLevels.find((level) => level.value === selectedAccessLevel) || accessLevels[0];
    const accessQueryKey = ['pods', podId, 'resources', resourceType, resourceId, 'access'];
    const optionCopies = useMemo(
        () => options.map((option) => getResourceVisibilityCopy(option, resourceLabel)),
        [options, resourceLabel],
    );
    const { data: accessData, isLoading: isAccessLoading } = useQuery({
        queryKey: accessQueryKey,
        queryFn: () => getLemmaClient(podId!).resourceAccess.get(resourceType!, resourceId!) as Promise<ResourceAccessResponse>,
        enabled: open && canManageSpecificAccess,
    });
    const { data: membersData } = useQuery({
        queryKey: ['pods', podId, 'members'],
        queryFn: () => getLemmaClient().podMembers.list(podId!) as Promise<{ items: PodMemberResponse[] }>,
        enabled: open && canManageSpecificAccess,
    });
    const grants = accessData?.grants || [];
    const members = membersData?.items || [];
    const memberOptions = members.map((member) => ({
        value: `POD_MEMBER:${member.pod_member_id}`,
        label: member.user_name || member.email || member.user_email,
        detail: member.email || member.user_email,
        granteeType: 'POD_MEMBER',
        granteeId: member.pod_member_id,
        grant: {
            resource_type: resourceType,
            resource_name: resourceId,
            grantee_type: 'POD_MEMBER',
            grantee_id: member.pod_member_id,
            permission_ids: selectedAccess?.permissionIds || [],
            user_id: member.user_id,
            email: member.email || member.user_email || null,
            display_name: member.user_name || member.email || member.user_email || null,
        } as ResourceAccessGrantResponse,
    }));
    const granteeOptions = memberOptions;
    const directAccessEnabled = draftVisibility !== 'PERSONAL';
    const effectiveDraftGrants = draftVisibility === 'PERSONAL'
        ? []
        : draftGrants.filter((grant) => grant.grantee_type === 'POD_MEMBER');
    const removedRoleGrantCount = directAccessEnabled
        ? draftGrants.filter((grant) => grant.grantee_type === 'ROLE').length
        : 0;
    const removedPersonalGrantCount = draftVisibility === 'PERSONAL' ? draftGrants.length : 0;
    const hasGrantChanges = canManageSpecificAccess && Boolean(accessData) && !sameGrantLists(grants, effectiveDraftGrants);
    const hasChanges = hasVisibilityChange || hasGrantChanges;
    const accessSectionTitle = draftVisibility === 'RESTRICTED' ? 'People with access' : 'Additional people';
    const accessEmptyCopy = 'No specific people yet.';
    const accessSelectCopy = 'Add a person';
    const accessSectionDescription = draftVisibility === 'RESTRICTED'
        ? 'Only these people can open it.'
        : 'People added here get direct access in addition to workspace access.';

    const saveSharing = useMutation({
        mutationFn: async () => {
            if (hasVisibilityChange) {
                await onChange(draftVisibility);
            }

            if (!canManageSpecificAccess || !accessData || !podId || !resourceType || !resourceId) {
                return null;
            }

            const finalByKey = new Map(effectiveDraftGrants.map((grant) => [grantKey(grant), grant]));
            const initialByKey = new Map(grants.map((grant) => [grantKey(grant), grant]));
            const grantsToDelete = grants.filter((grant) => !finalByKey.has(grantKey(grant)));
            const grantsToReplace = effectiveDraftGrants.filter((grant) => {
                const initial = initialByKey.get(grantKey(grant));
                return !initial || !samePermissions(initial.permission_ids || [], grant.permission_ids || []);
            });

            await Promise.all([
                ...grantsToDelete.map((grant) =>
                    getLemmaClient(podId).resourceAccess.deleteGrant(
                        resourceType,
                        resourceId,
                        grant.grantee_type,
                        grant.grantee_id,
                    )
                ),
                ...grantsToReplace.map((grant) =>
                    getLemmaClient(podId).resourceAccess.replaceGrant(
                        resourceType,
                        resourceId,
                        grant.grantee_type,
                        grant.grantee_id,
                        { permission_ids: grant.permission_ids || [] },
                    )
                ),
            ]);

            return null;
        },
        onSuccess: () => {
            setSaveError(null);
            setOpen(false);
            void queryClient.invalidateQueries({ queryKey: accessQueryKey });
        },
        onError: (error) => {
            setSaveError(error instanceof Error ? error.message : 'Failed to save sharing changes.');
        },
    });

    useEffect(() => {
        if (!open || !accessData) return;
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setDraftGrants(accessData.grants || []);
    }, [accessData, open]);

    const addDraftGrant = (granteeValue: string, access = selectedAccess) => {
        if (granteeValue === NO_GRANTEE_VALUE || !access) return;
        const option = granteeOptions.find((candidate) => candidate.value === granteeValue);
        if (!option) return;
        const nextGrant = {
            ...option.grant,
            permission_ids: access.permissionIds,
        };
        setDraftGrants((prev) => [
            ...prev.filter((grant) => grantKey(grant) !== grantKey(nextGrant)),
            nextGrant,
        ]);
        setSelectedGrantee(NO_GRANTEE_VALUE);
    };

    const handleSelectedGranteeChange = (nextGrantee: string) => {
        setSelectedGrantee(nextGrantee);
        addDraftGrant(nextGrantee);
    };

    const handleRemoveDraftGrant = (grant: ResourceAccessGrantResponse) => {
        setDraftGrants((prev) => prev.filter((candidate) => grantKey(candidate) !== grantKey(grant)));
    };

    const handleDraftGrantAccessChange = (grant: ResourceAccessGrantResponse, accessLevel: string) => {
        const nextAccess = accessLevels.find((level) => level.value === accessLevel);
        if (!nextAccess) return;
        setDraftGrants((prev) => prev.map((candidate) => (
            grantKey(candidate) === grantKey(grant)
                ? { ...candidate, permission_ids: nextAccess.permissionIds }
                : candidate
        )));
    };

    const handleOpenChange = (nextOpen: boolean) => {
        setDraftVisibility(current);
        setDraftGrants(accessData?.grants || []);
        setSelectedGrantee(NO_GRANTEE_VALUE);
        setSelectedAccessLevel('viewer');
        setSaveError(null);
        setOpen(nextOpen);
    };

    const triggerNode = trigger?.({ openShare: () => handleOpenChange(true), disabled }) ?? (
        <Button
            type="button"
            variant="outline"
            size="sm"
            className={cn('h-8 gap-2 rounded-full px-3 text-sm font-medium', buttonClassName)}
            onClick={() => handleOpenChange(true)}
            disabled={disabled}
        >
            <Share2 className="h-3.5 w-3.5" />
            Share
        </Button>
    );

    const handleDone = () => {
        if (!hasChanges) {
            setOpen(false);
            return;
        }
        void saveSharing.mutate();
    };

    const copyLink = async () => {
        if (!shareUrl) return;
        try {
            await navigator.clipboard.writeText(shareUrl);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1600);
        } catch {
            setCopied(false);
        }
    };

    return (
        <div className={className}>
            {triggerNode}

            <Dialog open={open} onOpenChange={handleOpenChange}>
                <DialogContent className="w-[calc(100vw-2rem)] max-w-[640px] gap-0 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] p-0 shadow-[var(--shadow-lg)] duration-0 data-[state=closed]:animate-none data-[state=open]:animate-none">
                    <DialogHeader className="border-b border-[var(--border-subtle)] px-6 py-5">
                        <DialogTitle className="text-2xl font-medium leading-8 text-[var(--text-primary)]">Share</DialogTitle>
                        {resourceName ? (
                            <DialogDescription className="mt-1.5 truncate text-base leading-6 text-[var(--text-secondary)]">
                                {resourceName}
                            </DialogDescription>
                        ) : null}
                    </DialogHeader>

                    <div className="space-y-6 px-6 py-5">
                        <section className="space-y-3">
                            <div className="flex items-center justify-between gap-3">
                                <h3 className="flex items-center gap-1.5 text-base font-medium text-[var(--text-primary)]">General access<ConceptHint concept="grant" /></h3>
                                {hasVisibilityChange ? (
                                    <span className="text-sm text-[var(--state-warning)]">Unsaved</span>
                                ) : null}
                            </div>
                            <div className="flex items-center gap-4 rounded-md bg-[var(--surface-2)] px-4 py-3">
                                <span className={cn(
                                    'flex h-10 w-10 shrink-0 items-center justify-center rounded-full',
                                    draftVisibility === 'RESTRICTED'
                                        ? 'bg-[var(--bg-muted)] text-[var(--text-secondary)]'
                                        : 'bg-[var(--action-primary-soft)] text-[var(--action-primary)]',
                                )}>
                                    <DraftIcon className="h-5 w-5" />
                                </span>
                                <div className="min-w-0 flex-1">
                                    <Select value={draftVisibility} onValueChange={(next) => setDraftVisibility(next as ResourceVisibilityValue)}>
                                        <SelectTrigger className="h-10 w-fit min-w-48 rounded-md border border-[var(--field-border)] bg-[var(--field-bg)] px-4 py-0 text-base font-medium shadow-none">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent className="min-w-56 p-1.5">
                                            {optionCopies.map((option) => (
                                                <SelectItem key={option.value} value={option.value} className="px-3 py-2.5">
                                                    {option.label}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <p className="mt-0.5 text-sm leading-5 text-[var(--text-secondary)]">
                                        {draftCopy.description}
                                    </p>
                                    {draftVisibility === 'PUBLIC' && !shareUrl ? (
                                        <p className="mt-2 text-xs leading-5 text-[var(--text-tertiary)]">
                                            A link will be available after this resource is created.
                                        </p>
                                    ) : null}
                                </div>
                            </div>
                        </section>

                        {canManageSpecificAccess && directAccessEnabled ? (
                            <section className="space-y-4">
                                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_9rem]">
                                    <Select value={selectedGrantee} onValueChange={handleSelectedGranteeChange}>
                                        <SelectTrigger className="h-12 min-w-0 rounded-md bg-[var(--surface-1)] px-4 text-left text-base">
                                            <SelectValue placeholder={accessSelectCopy} />
                                        </SelectTrigger>
                                        <SelectContent className="min-w-[22rem] p-1.5">
                                            <SelectItem value={NO_GRANTEE_VALUE} className="px-3 py-2.5">
                                                {accessSelectCopy}
                                            </SelectItem>
                                            {granteeOptions.map((option) => (
                                                <SelectItem key={option.value} value={option.value} className="px-3 py-2.5">
                                                    <span className="flex min-w-0 flex-col gap-0.5">
                                                        <span className="truncate text-sm font-medium text-[var(--text-primary)]">{option.label}</span>
                                                        <span className="truncate text-xs text-[var(--text-tertiary)]">{option.detail}</span>
                                                    </span>
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <Select value={selectedAccessLevel} onValueChange={setSelectedAccessLevel}>
                                        <SelectTrigger className="h-12 rounded-md bg-[var(--surface-1)] px-4 text-base">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent className="min-w-44 p-1.5">
                                            {accessLevels.map((level) => (
                                                <SelectItem key={level.value} value={level.value} className="px-3 py-2.5">
                                                    {level.label}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>

                                <div className="space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                        <div className="min-w-0">
                                            <h3 className="text-base font-medium text-[var(--text-primary)]">{accessSectionTitle}</h3>
                                            <p className="mt-0.5 text-sm leading-5 text-[var(--text-secondary)]">
                                                {accessSectionDescription}
                                            </p>
                                        </div>
                                        {isAccessLoading ? (
                                            <span className="text-xs text-[var(--text-tertiary)]">Loading</span>
                                        ) : null}
                                    </div>
                                    <div className="space-y-2">
                                        {effectiveDraftGrants.length === 0 ? (
                                            <p className="rounded-md border border-dashed border-[var(--border-subtle)] px-4 py-3 text-base leading-6 text-[var(--text-secondary)]">
                                                {accessEmptyCopy}
                                            </p>
                                        ) : (
                                            effectiveDraftGrants.map((grant) => (
                                                <div
                                                    key={`${grant.grantee_type}:${grant.grantee_id}`}
                                                    className="flex items-center gap-3 rounded-md px-1 py-2"
                                                >
                                                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--bg-muted)] text-sm font-semibold text-[var(--text-secondary)]">
                                                        {grant.grantee_type === 'ROLE' ? <UsersRound className="h-4 w-4" /> : getGrantInitials(grant)}
                                                    </span>
                                                    <div className="min-w-0 flex-1">
                                                        <div className="truncate text-base font-medium text-[var(--text-primary)]">{getGrantLabel(grant)}</div>
                                                        <div className="truncate text-sm text-[var(--text-tertiary)]">{grant.email || getAccessLabel(resourceType!, grant.permission_ids || [])}</div>
                                                    </div>
                                                    <Select
                                                        value={accessLevels.find((level) => samePermissions(level.permissionIds, grant.permission_ids || []))?.value || ''}
                                                        onValueChange={(next) => handleDraftGrantAccessChange(grant, next)}
                                                    >
                                                        <SelectTrigger className="h-9 w-28 rounded-md bg-[var(--surface-1)] px-3 text-sm">
                                                            <SelectValue placeholder={getAccessLabel(resourceType!, grant.permission_ids || [])} />
                                                        </SelectTrigger>
                                                        <SelectContent className="min-w-36 p-1.5">
                                                            {accessLevels.map((level) => (
                                                                <SelectItem key={level.value} value={level.value} className="px-3 py-2.5">
                                                                    {level.label}
                                                                </SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                    <Button
                                                        type="button"
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-9 w-9"
                                                        onClick={() => handleRemoveDraftGrant(grant)}
                                                        disabled={saveSharing.isPending}
                                                        title={`Remove ${getGrantLabel(grant)}`}
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            ))
                                        )}
                                        {removedRoleGrantCount > 0 ? (
                                            <p className="text-xs leading-5 text-[var(--state-warning)]">
                                                Role-based access is not available here and will be removed when you save.
                                            </p>
                                        ) : null}
                                    </div>
                                </div>
                            </section>
                        ) : null}
                        {canManageSpecificAccess && !directAccessEnabled && removedPersonalGrantCount > 0 ? (
                            <p className="rounded-md bg-[var(--surface-2)] px-4 py-3 text-sm leading-5 text-[var(--state-warning)]">
                                Existing direct access will be removed when you save.
                            </p>
                        ) : null}
                        {saveError ? (
                            <p className="text-sm leading-5 text-[var(--state-error)]">{saveError}</p>
                        ) : null}
                    </div>

                    <DialogFooter className="border-t border-[var(--border-subtle)] px-6 py-5 sm:justify-between">
                        <Button
                            type="button"
                            variant="outline"
                            className="h-11 gap-2 rounded-full px-5 text-base"
                            onClick={() => void copyLink()}
                            disabled={!shareUrl}
                            title={shareUrl ? 'Copy resource link' : 'Create this resource before copying a link'}
                        >
                            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                            {copied ? 'Copied' : 'Copy link'}
                        </Button>
                        <Button
                            type="button"
                            className="h-11 rounded-full px-7 text-base"
                            onClick={handleDone}
                            disabled={saveSharing.isPending || (canManageSpecificAccess && isAccessLoading)}
                        >
                            {saveSharing.isPending ? 'Saving' : hasChanges ? 'Save' : 'Done'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

export const ResourceVisibilitySelect = ResourceShareButton;
