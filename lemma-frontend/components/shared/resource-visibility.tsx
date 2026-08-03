'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Globe2, LockKeyhole, Share2, Trash2, UserRound, UsersRound, type LemmaIcon } from '@/components/ui/icons';
import type { PodMemberResponse, ResourceAccessGrantResponse, ResourceAccessResponse } from 'lemma-sdk';

import { ConceptHint } from '@/components/education/concept-hint';
import { ShareLinkRow } from '@/components/share/share-link-row';
import { SocialCardPanel } from '@/components/share/social-card-panel';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { buildShareLink, shareKindForResourceType } from '@/lib/share/share-link';
import type { SocialCardVariant } from '@/lib/share/social-card';
import { cn } from '@/lib/utils';

// The scale itself lives in lib/share so it can be tested without this
// component's React/Query surface; re-exported below so existing importers of
// `ResourceVisibilityValue` / `normalizeResourceVisibility` are unaffected.
import {
    normalizeResourceVisibility,
    REACHES_OUTSIDE_POD,
    VISIBILITY_VALUES,
    type ResourceVisibilityValue,
} from '@/lib/share/resource-visibility';

export {
    normalizeResourceVisibility,
    REACHES_OUTSIDE_POD,
    VISIBILITY_VALUES,
    type ResourceVisibilityValue,
};

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

const NO_GRANTEE_VALUE = '__none__';

/**
 * Only the things a stranger can meaningfully open get a social card. A table
 * or a folder is shared *into* a team, not out to a timeline, and "Run it on
 * Lemma" would be a lie on that card.
 */
const SOCIAL_CARD_VARIANT_BY_RESOURCE: Partial<Record<ShareableResourceType, SocialCardVariant>> = {
    agent: 'agent',
    app: 'app',
    workflow: 'workflow',
};

/** Human name for the kind of thing being shared, shown under the dialog title. */
const RESOURCE_NOUN: Record<ShareableResourceType, string> = {
    agent: 'Agent',
    function: 'Function',
    workflow: 'Workflow',
    schedule: 'Schedule',
    datastore_table: 'Table',
    document: 'Document',
    folder: 'Folder',
    app: 'App',
};

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

    if (visibility === 'ORGANIZATION') {
        return {
            value: visibility,
            label: 'Everyone at work',
            shortDescription: 'Anyone in your organization',
            description: 'Anyone in your organization can open it, pod member or not.',
            icon: Building2,
            className: 'state-badge-info',
        };
    }

    if (visibility === 'PUBLIC') {
        return {
            value: visibility,
            // Not anonymous: authorization still runs against a signed-in
            // principal, so this waives org scope rather than opening the
            // resource to the open internet. The copy has to say so.
            label: 'Anyone signed in',
            shortDescription: 'Anyone with a Lemma account',
            description: 'Anyone with a Lemma account can open it, including outside your organization.',
            icon: Globe2,
            className: 'state-badge-warning',
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
    ORGANIZATION: 'text-[var(--state-info)]',
    // Warning, not info: this is the only level that leaves the organization.
    PUBLIC: 'text-[var(--state-warning)]',
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

/**
 * One choice in the general-access list.
 *
 * All four options stay on screen instead of hiding behind a dropdown — the
 * whole point of this dialog is comparing "only me" against "anyone with the
 * link", and you cannot compare what you cannot see.
 */
function VisibilityOption({
    copy,
    selected,
    onSelect,
}: {
    copy: ResourceVisibilityCopy;
    selected: boolean;
    onSelect: () => void;
}) {
    const Icon = copy.icon;

    return (
        <label
            className={cn(
                'flex cursor-pointer items-center gap-3 rounded-md border px-3 py-2.5 transition-gentle',
                selected
                    ? 'border-[color:var(--action-primary)] bg-[var(--action-primary-soft)]'
                    : 'border-[color:var(--border-subtle)] bg-[var(--surface-1)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)]',
            )}
        >
            <RadioGroupItem value={copy.value} id={`visibility-${copy.value}`} onClick={onSelect} />
            <span
                className={cn(
                    'flex h-8 w-8 shrink-0 items-center justify-center rounded-md',
                    selected
                        ? 'bg-[var(--action-primary)] text-[var(--text-on-brand)]'
                        : 'bg-[var(--surface-3)] text-[var(--text-tertiary)]',
                )}
            >
                <Icon className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-[var(--text-primary)]">
                    {copy.label}
                </span>
                <span className="block truncate text-xs text-[var(--text-secondary)]">
                    {copy.description}
                </span>
            </span>
        </label>
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
    const [draftVisibility, setDraftVisibility] = useState<ResourceVisibilityValue>(current);
    const [selectedGrantee, setSelectedGrantee] = useState<string>(NO_GRANTEE_VALUE);
    const [selectedAccessLevel, setSelectedAccessLevel] = useState<string>('viewer');
    const [draftGrants, setDraftGrants] = useState<ResourceAccessGrantResponse[]>([]);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [hasAcknowledgedPublic, setHasAcknowledgedPublic] = useState(false);
    const [inviteEmail, setInviteEmail] = useState('');
    const cardSectionRef = useRef<HTMLElement | null>(null);
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
    const grants = useMemo(() => accessData?.grants || [], [accessData]);
    const members = membersData?.items || [];
    const granteeOptions = members.map((member) => ({
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
    const directAccessEnabled = draftVisibility !== 'PERSONAL';
    const effectiveDraftGrants = useMemo(
        () => (draftVisibility === 'PERSONAL'
            ? []
            : draftGrants.filter((grant) => grant.grantee_type === 'POD_MEMBER')),
        [draftVisibility, draftGrants],
    );
    const removedRoleGrantCount = directAccessEnabled
        ? draftGrants.filter((grant) => grant.grantee_type === 'ROLE').length
        : 0;
    const removedPersonalGrantCount = draftVisibility === 'PERSONAL' ? draftGrants.length : 0;
    const hasGrantChanges = canManageSpecificAccess && Boolean(accessData) && !sameGrantLists(grants, effectiveDraftGrants);
    const hasChanges = hasVisibilityChange || hasGrantChanges;
    /**
     * Leaving the organization is the one step here that cannot be walked back
     * by editing a member list, so it is the one step that asks twice. Only on
     * newly selecting it — reopening the dialog on an already-public resource
     * does not re-prompt.
     */
    const needsPublicConfirmation = draftVisibility === 'PUBLIC' && current !== 'PUBLIC';
    const isBlockedOnConfirmation = needsPublicConfirmation && !hasAcknowledgedPublic;
    const accessSectionTitle = draftVisibility === 'RESTRICTED' ? 'People with access' : 'Additional people';
    const accessSectionDescription = draftVisibility === 'RESTRICTED'
        ? 'Only these people can open it.'
        : 'Added on top of workspace access.';

    // Granted people already in the list should not be offered again.
    const grantedKeys = new Set(effectiveDraftGrants.map((grant) => grantKey(grant)));
    const addableOptions = granteeOptions.filter(
        (option) => !grantedKeys.has(`${option.granteeType}:${option.granteeId}`),
    );

    const cardVariant = resourceType ? SOCIAL_CARD_VARIANT_BY_RESOURCE[resourceType] : undefined;

    /**
     * A `/pod/…` URL is signed-in-only and drops anyone without pod access on a
     * "request access" wall, whatever the resource's own visibility says. Once
     * the audience reaches past the pod, hand out the `/s/…` wrapper instead:
     * it can render the resource for someone who is allowed to read it but is
     * not a member, and anyone who *does* have pod access is redirected straight
     * through to this same URL. It also unfurls, which `/pod/…` never did.
     */
    const outsidePodShareUrl = useMemo(() => {
        if (!shareUrl || !resourceType) return null;
        if (!REACHES_OUTSIDE_POD.includes(draftVisibility)) return null;
        return buildShareLink({
            kind: shareKindForResourceType(resourceType),
            canonicalUrl: shareUrl,
            name: resourceName,
        });
    }, [shareUrl, resourceType, resourceName, draftVisibility]);

    const linkToShare = outsidePodShareUrl ?? shareUrl;
    // Only a genuinely public resource gets a card. An org-visible one would
    // unfurl its name into whatever timeline the link was pasted into.
    const canShowCard = Boolean(cardVariant && outsidePodShareUrl && draftVisibility === 'PUBLIC');

    const inviteByEmail = useMutation({
        mutationFn: async () => {
            if (!podId || !resourceType || !resourceId) return null;
            await getLemmaClient(podId).resourceAccess.requestInvite({
                resource_type: resourceType as never,
                resource_name: resourceId,
                email: inviteEmail.trim(),
                permission_ids: selectedAccess?.permissionIds || [],
            });
            setInviteEmail('');
            return null;
        },
    });

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

    // Switching to "anyone signed in" reveals the card further up the dialog;
    // bring it into view so the change is visible wherever the reader is.
    useEffect(() => {
        if (!canShowCard) return;
        cardSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, [canShowCard]);

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
        setHasAcknowledgedPublic(false);
        setOpen(nextOpen);
    };

    const triggerNode = trigger?.({ openShare: () => handleOpenChange(true), disabled }) ?? (
        <Button
            type="button"
            variant="secondary"
            size="sm"
            className={cn('gap-1.5', buttonClassName)}
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
        if (isBlockedOnConfirmation) return;
        void saveSharing.mutate();
    };

    const resourceNoun = resourceType ? RESOURCE_NOUN[resourceType] : null;

    return (
        <div className={className}>
            {triggerNode}

            <Dialog open={open} onOpenChange={handleOpenChange}>
                <DialogContent className="max-w-[560px] gap-0 overflow-hidden p-0">
                    <DialogHeader className="gap-1 border-b border-[color:var(--border-subtle)] px-5 py-4 pr-12 text-left">
                        <DialogTitle className="truncate">
                            {resourceName ? `Share ${resourceName}` : 'Share'}
                        </DialogTitle>
                        <DialogDescription className="text-xs">
                            {resourceNoun
                                ? `${resourceNoun} · choose who can open it and what they can do.`
                                : 'Choose who can open it and what they can do.'}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="max-h-[min(70dvh,34rem)] space-y-5 overflow-y-auto px-5 py-4">
                        <ShareLinkRow
                            url={linkToShare}
                            name={resourceName}
                            allowNativeShare={draftVisibility === 'PUBLIC'}
                            emptyHint="A link is available once this is created."
                        />

                        {/* Once it is open to anyone signed in, the card *is* the
                            share — so it sits with the link rather than behind a
                            toggle at the bottom of a scrolling dialog. */}
                        {canShowCard && cardVariant ? (
                            <section ref={cardSectionRef}>
                                <SocialCardPanel
                                    layout="compact"
                                    variant={cardVariant}
                                    name={resourceName}
                                    url={outsidePodShareUrl}
                                    unfurls
                                />
                            </section>
                        ) : null}

                        <section className="space-y-2">
                            <div className="flex items-center justify-between gap-3">
                                <h3 className="flex items-center gap-1.5 text-sm font-medium text-[var(--text-primary)]">
                                    General access
                                    <ConceptHint concept="grant" />
                                </h3>
                                {hasVisibilityChange ? (
                                    <span className="text-xs text-[var(--state-warning)]">Unsaved</span>
                                ) : null}
                            </div>
                            <RadioGroup
                                className="gap-1.5"
                                value={draftVisibility}
                                onValueChange={(next) => setDraftVisibility(next as ResourceVisibilityValue)}
                            >
                                {optionCopies.map((option) => (
                                    <VisibilityOption
                                        key={option.value}
                                        copy={option}
                                        selected={draftVisibility === option.value}
                                        onSelect={() => setDraftVisibility(option.value)}
                                    />
                                ))}
                            </RadioGroup>

                            {needsPublicConfirmation ? (
                                <label className="state-surface-warning flex cursor-pointer items-start gap-2.5 rounded-md px-3 py-2.5">
                                    <Checkbox
                                        className="mt-0.5 shrink-0"
                                        checked={hasAcknowledgedPublic}
                                        onCheckedChange={(checked) =>
                                            setHasAcknowledgedPublic(checked === true)
                                        }
                                    />
                                    <span className="text-xs text-[var(--text-secondary)]">
                                        This leaves your organization. Anyone with a Lemma account —
                                        including people you do not work with — will be able to open{' '}
                                        {resourceName ? <strong>{resourceName}</strong> : 'this'} using the
                                        link.
                                    </span>
                                </label>
                            ) : null}
                        </section>

                        {canManageSpecificAccess && directAccessEnabled ? (
                            <section className="space-y-2">
                                <div className="flex items-baseline justify-between gap-3">
                                    <h3 className="text-sm font-medium text-[var(--text-primary)]">
                                        {accessSectionTitle}
                                    </h3>
                                    <span className="text-xs text-[var(--text-tertiary)]">
                                        {isAccessLoading ? 'Loading…' : accessSectionDescription}
                                    </span>
                                </div>

                                <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_8rem]">
                                    <Select
                                        value={selectedGrantee}
                                        onValueChange={handleSelectedGranteeChange}
                                        disabled={addableOptions.length === 0}
                                    >
                                        {/* SelectTrigger line-clamps its direct span child, which
                                            collapses any flex layout nested inside it — so the
                                            trigger keeps a plain SelectValue and nothing else. */}
                                        <SelectTrigger className="h-9 min-w-0 text-sm">
                                            <SelectValue
                                                placeholder={
                                                    addableOptions.length === 0
                                                        ? 'Everyone here already has access'
                                                        : 'Add a person…'
                                                }
                                            />
                                        </SelectTrigger>
                                        <SelectContent className="min-w-[20rem]">
                                            {addableOptions.map((option) => (
                                                <SelectItem key={option.value} value={option.value}>
                                                    <span className="flex min-w-0 flex-col">
                                                        <span className="truncate text-sm text-[var(--text-primary)]">
                                                            {option.label}
                                                        </span>
                                                        <span className="truncate text-xs text-[var(--text-tertiary)]">
                                                            {option.detail}
                                                        </span>
                                                    </span>
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <Select value={selectedAccessLevel} onValueChange={setSelectedAccessLevel}>
                                        <SelectTrigger className="h-9 text-sm">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {accessLevels.map((level) => (
                                                <SelectItem key={level.value} value={level.value}>
                                                    {level.label}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>

                                {/* Someone outside the pod — possibly without an
                                    account at all. Held as an invite against the
                                    address and redeemed into a real grant once
                                    that address is verified, so sharing outward
                                    no longer means adding them to the org. */}
                                <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                                    <Input
                                        type="email"
                                        value={inviteEmail}
                                        onChange={(event) => setInviteEmail(event.target.value)}
                                        placeholder="Or invite by email…"
                                        className="h-9 min-w-0"
                                    />
                                    <Button
                                        type="button"
                                        variant="secondary"
                                        size="sm"
                                        className="h-9"
                                        loading={inviteByEmail.isPending}
                                        loadingLabel="Inviting"
                                        disabled={!inviteEmail.includes('@')}
                                        onClick={() => inviteByEmail.mutate()}
                                    >
                                        Invite
                                    </Button>
                                </div>
                                {inviteByEmail.isSuccess ? (
                                    <p className="text-xs text-[var(--text-tertiary)]">
                                        Invited. They will get access when they sign in with that
                                        address.
                                    </p>
                                ) : null}
                                {inviteByEmail.isError ? (
                                    <p className="text-xs text-[var(--state-error)]">
                                        Could not send that invite.
                                    </p>
                                ) : null}

                                {effectiveDraftGrants.length === 0 ? (
                                    <p className="rounded-md border border-dashed border-[color:var(--border-subtle)] px-3 py-2.5 text-xs text-[var(--text-tertiary)]">
                                        {draftVisibility === 'RESTRICTED'
                                            ? 'No one can open this yet — add the people who need it.'
                                            : 'No one has been added directly.'}
                                    </p>
                                ) : (
                                    <ul className="divide-y divide-[color:var(--border-subtle)] rounded-md border border-[color:var(--border-subtle)] bg-[var(--surface-1)]">
                                        {effectiveDraftGrants.map((grant) => (
                                            <li
                                                key={grantKey(grant)}
                                                className="flex items-center gap-3 px-3 py-2"
                                            >
                                                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--surface-3)] text-xs font-semibold text-[var(--text-secondary)]">
                                                    {grant.grantee_type === 'ROLE' ? <UsersRound className="h-4 w-4" /> : getGrantInitials(grant)}
                                                </span>
                                                <div className="min-w-0 flex-1">
                                                    <div className="truncate text-sm font-medium text-[var(--text-primary)]">
                                                        {getGrantLabel(grant)}
                                                    </div>
                                                    <div className="truncate text-xs text-[var(--text-tertiary)]">
                                                        {grant.email || getAccessLabel(resourceType!, grant.permission_ids || [])}
                                                    </div>
                                                </div>
                                                <Select
                                                    value={accessLevels.find((level) => samePermissions(level.permissionIds, grant.permission_ids || []))?.value || ''}
                                                    onValueChange={(next) => handleDraftGrantAccessChange(grant, next)}
                                                >
                                                    <SelectTrigger className="h-8 w-[6.5rem] text-xs">
                                                        <SelectValue placeholder={getAccessLabel(resourceType!, grant.permission_ids || [])} />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {accessLevels.map((level) => (
                                                            <SelectItem key={level.value} value={level.value}>
                                                                {level.label}
                                                            </SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                                <Button
                                                    type="button"
                                                    variant="quiet"
                                                    size="icon"
                                                    className="h-8 w-8 shrink-0"
                                                    onClick={() => handleRemoveDraftGrant(grant)}
                                                    disabled={saveSharing.isPending}
                                                    aria-label={`Remove ${getGrantLabel(grant)}`}
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </li>
                                        ))}
                                    </ul>
                                )}

                                {removedRoleGrantCount > 0 ? (
                                    <p className="text-xs text-[var(--state-warning)]">
                                        Role-based access is not available here and will be removed when you save.
                                    </p>
                                ) : null}
                            </section>
                        ) : null}

                        {canManageSpecificAccess && !directAccessEnabled && removedPersonalGrantCount > 0 ? (
                            <p className="rounded-md bg-[var(--surface-2)] px-3 py-2.5 text-xs text-[var(--state-warning)]">
                                Existing direct access will be removed when you save.
                            </p>
                        ) : null}

                        {saveError ? (
                            <p className="text-sm text-[var(--state-error)]">{saveError}</p>
                        ) : null}
                    </div>

                    <DialogFooter className="items-center border-t border-[color:var(--border-subtle)] px-5 py-3 sm:justify-end">
                        <Button type="button" variant="secondary" size="sm" onClick={() => setOpen(false)}>
                            Cancel
                        </Button>
                        <Button variant="primary"
                            type="button"
                            size="sm"
                            onClick={handleDone}
                            loading={saveSharing.isPending}
                            loadingLabel="Saving"
                            disabled={(canManageSpecificAccess && isAccessLoading) || isBlockedOnConfirmation}
                        >
                            {hasChanges ? 'Save' : 'Done'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

export const ResourceVisibilitySelect = ResourceShareButton;
