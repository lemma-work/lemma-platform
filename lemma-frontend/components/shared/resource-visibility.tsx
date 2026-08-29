'use client';

import { useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronLeft, ChevronRight, Globe2, LockKeyhole, Share2, Trash2, UserRound, UsersRound, type LemmaIcon } from '@/components/ui/icons';
import type { PodMemberResponse, ResourceAccessGrantResponse, ResourceAccessResponse } from 'lemma-sdk';

import { ConceptHint } from '@/components/education/concept-hint';
import { ShareLinkRow } from '@/components/share/share-link-row';
import { SocialCardPanel } from '@/components/share/social-card-panel';
import { StepLoader } from '@/components/brand/loader';
import { Command, CommandInput } from '@/components/ui/command';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover';
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
    defaultVisibilityFor,
    normalizeResourceVisibility,
    reachesOutsidePod,
    VISIBILITY_VALUES,
    type ResourceVisibilityValue,
} from '@/lib/share/resource-visibility';

export {
    defaultVisibilityFor,
    normalizeResourceVisibility,
    reachesOutsidePod,
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
    resourceType?: ShareableResourceType | null,
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
        // An app is the one resource where PUBLIC really does mean the open
        // internet: the page is served by public host to browsers with no
        // session at all. Everywhere else authorization still runs against a
        // signed-in principal, so PUBLIC waives pod scope rather than opening
        // the resource up. Two different promises, so two different sentences
        // -- this is the copy someone reads before deciding.
        if (resourceType === 'app') {
            return {
                value: visibility,
                label: 'On the web',
                shortDescription: 'Anyone with the link',
                description:
                    'Anyone with the link can load this app, no sign-in needed. Its data still requires access — visitors are asked to sign in or request it.',
                icon: Globe2,
                className: 'state-badge-warning',
            };
        }
        return {
            value: visibility,
            // Not anonymous: authorization still runs against a signed-in
            // principal, so this waives pod scope rather than opening the
            // resource to the open internet. The copy has to say so.
            label: 'Anyone signed in',
            shortDescription: 'Anyone with a Lemma account',
            description: 'Anyone with a Lemma account can open it, including people outside your team.',
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
    // Warning, not info: it is the only level that reaches past the pod.
    PUBLIC: 'text-[var(--state-warning)]',
    POD: 'text-[var(--text-tertiary)]',
};

export function ResourceVisibilityBadge({
    visibility,
    resourceLabel,
    resourceType,
    className,
    compact = false,
    hideWhenDefault,
}: {
    visibility?: string | null;
    resourceLabel?: string;
    /** Which kind of resource this is. Decides both the PUBLIC wording and which level counts as the default worth hiding — apps differ on both. */
    resourceType?: ShareableResourceType | null;
    className?: string;
    compact?: boolean;
    /** When the value is this resource's default, render nothing. Defaults to `compact` — dense list rows hide the common case so only exceptions stand out. */
    hideWhenDefault?: boolean;
}) {
    const copy = getResourceVisibilityCopy(visibility, resourceLabel, resourceType);
    const Icon = copy.icon;
    const tone = VISIBILITY_TONE[copy.value] ?? 'text-[var(--text-tertiary)]';
    const shouldHideDefault = hideWhenDefault ?? compact;

    // Per resource, not a hardcoded POD: apps are created PUBLIC, so hiding POD
    // there would silence the badge on exactly the app someone deliberately
    // took off the web, while flagging every untouched one.
    if (shouldHideDefault && copy.value === defaultVisibilityFor(resourceType)) {
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

/** Which of the three things the share popover is showing. */
type ShareView = 'access' | 'people' | 'card';

/**
 * One choice in the general-access list.
 *
 * All four options stay on screen instead of hiding behind a dropdown — the
 * whole point of this surface is comparing "only me" against "anyone with the
 * link", and you cannot compare what you cannot see.
 *
 * The row applies its own choice, so it also carries the answer: a spinner
 * while the change is in flight, a tick once it holds. That used to be a radio
 * you set and a Save button somewhere below that told you nothing about why it
 * was grey.
 */
function VisibilityOption({
    copy,
    selected,
    saving,
    onSelect,
    children,
}: {
    copy: ResourceVisibilityCopy;
    selected: boolean;
    saving: boolean;
    onSelect: () => void;
    /** The inline confirmation, when this is the choice being confirmed. */
    children?: ReactNode;
}) {
    const Icon = copy.icon;

    return (
        <div
            className={cn(
                'rounded-md border transition-gentle',
                selected || children
                    ? 'border-[color:var(--action-primary)] bg-[var(--action-primary-soft)]'
                    : 'border-transparent',
            )}
        >
            <button
                type="button"
                data-visibility-row=""
                onClick={onSelect}
                className={cn(
                    'resource-share-choice-button flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors focus-visible:outline-none',
                    !selected && !children && 'hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)]',
                )}
            >
                <span
                    className={cn(
                        'flex h-7 w-7 shrink-0 items-center justify-center rounded-md',
                        selected
                            ? 'bg-[var(--action-primary)] text-[var(--text-on-brand)]'
                            : 'bg-[var(--surface-3)] text-[var(--text-tertiary)]',
                    )}
                >
                    <Icon className="h-3.5 w-3.5" />
                </span>
                <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-[var(--text-primary)]">{copy.label}</span>
                    <span className="block truncate text-xs text-[var(--text-tertiary)]">
                        {copy.shortDescription}
                    </span>
                </span>
                {saving ? (
                    <StepLoader size="sm" />
                ) : (
                    <Check
                        className={cn(
                            'size-3.5 shrink-0',
                            selected ? 'text-[var(--action-primary)]' : 'text-transparent',
                        )}
                    />
                )}
            </button>
            {children}
        </div>
    );
}

/** A row that leads to one of the other two views, in the picker's idiom. */
function ShareNavRow({
    label,
    meta,
    onClick,
}: {
    label: string;
    meta?: string;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className="resource-share-nav-button flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-sm text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none"
        >
            <span className="min-w-0 flex-1 truncate">{label}</span>
            {meta ? <span className="shrink-0 text-xs text-[var(--text-tertiary)]">{meta}</span> : null}
            <ChevronRight className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
        </button>
    );
}

/** The back arrow takes the leading slot, as it does in the model picker. */
function ShareViewHeader({
    title,
    onBack,
    children,
}: {
    title: string;
    onBack: () => void;
    children?: ReactNode;
}) {
    return (
        // The back arrow takes the search glyph's slot rather than sitting
        // beside it: two marks before the placeholder read as decoration, and
        // only one of them is a control.
        <div className="flex items-center gap-1 border-b border-[color:var(--border-subtle)] bg-[var(--surface-2)] px-1 [&_[cmdk-input-wrapper]]:min-w-0 [&_[cmdk-input-wrapper]]:flex-1 [&_[cmdk-input-wrapper]]:border-0 [&_[cmdk-input-wrapper]]:bg-transparent [&_[cmdk-input-wrapper]]:pl-0 [&_[cmdk-input-wrapper]_svg]:hidden">
            <Button
                type="button"
                variant="quiet"
                size="icon"
                onClick={onBack}
                aria-label="Back to general access"
                className="size-8 shrink-0 rounded-md text-[var(--text-tertiary)]"
            >
                <ChevronLeft className="size-4" />
            </Button>
            {children ?? (
                <span className="min-w-0 flex-1 truncate py-2 text-sm text-[var(--text-primary)]">{title}</span>
            )}
        </div>
    );
}

type GranteeType = ResourceAccessGrantResponse['grantee_type'];

/** One change to one person's access, applied on its own. */
type GrantOp = {
    kind: 'set' | 'remove';
    key: string;
    granteeType: GranteeType;
    granteeId: string;
    permissionIds?: string[];
};

/**
 * Share, as a popover.
 *
 * It was a 560px dialog with a scrolling body and a Save button, and the Save
 * button was the problem. It went grey for two unrelated reasons — the grants
 * request still in flight, and an unticked acknowledgement further down the
 * page — and said neither, so choosing "anyone signed in" looked like a click
 * that had failed. Meanwhile selecting it mounted the social card above the
 * list you had just clicked and smooth-scrolled the page out from under the
 * cursor while a ~1.3s image rendered into an empty box.
 *
 * So: no Save. Every choice applies when you make it, the row itself carries
 * the spinner and the tick, and the two steps that cannot be walked back ask
 * for confirmation *in the row you clicked* rather than through a checkbox
 * wired to a button somewhere else. The card moves behind the link it belongs
 * to, one view over, where its render time costs nothing.
 *
 * The shape is the model picker's: a short list you act on directly, and named
 * rows leading to the longer surfaces. Same reason, too — this is a thing
 * people open often and want to leave quickly.
 */
export function ResourceShareButton({
    value,
    onChange,
    podId,
    resourceType,
    resourceId,
    resourceLabel,
    resourceName,
    shareUrl,
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
    buttonClassName?: string;
    disabled?: boolean;
    options?: ResourceVisibilityValue[];
    trigger?: (props: { openShare: () => void; disabled: boolean }) => ReactNode;
}) {
    const current = normalizeResourceVisibility(value);
    const queryClient = useQueryClient();
    const [open, setOpen] = useState(false);
    const [view, setView] = useState<ShareView>('access');
    const [confirming, setConfirming] = useState<ResourceVisibilityValue | null>(null);
    /** What the rows show while the change is in flight, so a click lands now. */
    const [optimistic, setOptimistic] = useState<ResourceVisibilityValue | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [peopleQuery, setPeopleQuery] = useState('');

    const canManageSpecificAccess = Boolean(podId && resourceType && resourceId);
    const accessLevels = resourceType ? ACCESS_LEVELS_BY_RESOURCE[resourceType] : [];
    const accessQueryKey = ['pods', podId, 'resources', resourceType, resourceId, 'access'];
    const optionCopies = useMemo(
        () => options.map((option) => getResourceVisibilityCopy(option, resourceLabel, resourceType)),
        [options, resourceLabel, resourceType],
    );

    const { data: accessData, isLoading: isAccessLoading } = useQuery({
        queryKey: accessQueryKey,
        queryFn: () => getLemmaClient(podId!).resourceAccess.get(resourceType!, resourceId!) as Promise<ResourceAccessResponse>,
        enabled: open && canManageSpecificAccess,
    });
    // Only the people view needs the roster, and most opens never reach it.
    const { data: membersData } = useQuery({
        queryKey: ['pods', podId, 'members'],
        queryFn: () => getLemmaClient().podMembers.list(podId!) as Promise<{ items: PodMemberResponse[] }>,
        enabled: open && view === 'people' && canManageSpecificAccess,
    });

    const grants = useMemo(() => accessData?.grants || [], [accessData]);
    const memberGrants = useMemo(
        () => grants.filter((grant) => grant.grantee_type === 'POD_MEMBER'),
        [grants],
    );
    const members = useMemo(() => membersData?.items || [], [membersData?.items]);

    const shownVisibility = optimistic ?? current;
    const directAccessEnabled = shownVisibility !== 'PERSONAL';

    const applyVisibility = useMutation({
        mutationFn: async (next: ResourceVisibilityValue) => {
            await onChange(next);
            return next;
        },
        onMutate: (next: ResourceVisibilityValue) => {
            setError(null);
            setOptimistic(next);
        },
        onError: (mutationError) => {
            setOptimistic(null);
            setError(mutationError instanceof Error
                ? mutationError.message
                : 'Could not change who can open this.');
        },
    });

    const applyGrant = useMutation({
        mutationFn: async (op: GrantOp) => {
            const client = getLemmaClient(podId!);
            if (op.kind === 'remove') {
                await client.resourceAccess.deleteGrant(resourceType!, resourceId!, op.granteeType, op.granteeId);
                return;
            }
            await client.resourceAccess.replaceGrant(
                resourceType!,
                resourceId!,
                op.granteeType,
                op.granteeId,
                { permission_ids: op.permissionIds || [] },
            );
        },
        onMutate: () => setError(null),
        onError: (mutationError) => {
            setError(mutationError instanceof Error ? mutationError.message : 'Could not update access.');
        },
        onSettled: () => {
            void queryClient.invalidateQueries({ queryKey: accessQueryKey });
        },
    });

    const savingVisibility = applyVisibility.isPending ? applyVisibility.variables : null;
    const pendingGrantKey = applyGrant.isPending ? applyGrant.variables?.key ?? null : null;

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
        if (!reachesOutsidePod(shownVisibility)) return null;
        return buildShareLink({
            kind: shareKindForResourceType(resourceType),
            canonicalUrl: shareUrl,
            name: resourceName,
        });
    }, [shareUrl, resourceType, resourceName, shownVisibility]);

    const linkToShare = outsidePodShareUrl ?? shareUrl;
    const cardVariant = resourceType ? SOCIAL_CARD_VARIANT_BY_RESOURCE[resourceType] : undefined;
    // A card only exists where a link reaches past the pod — anything narrower
    // has no `/s/…` URL to unfurl in the first place.
    const canShowCard = Boolean(cardVariant && outsidePodShareUrl);

    const grantedKeys = new Set(grants.map((grant) => grantKey(grant)));
    const addableMembers = members.filter(
        (member) => !grantedKeys.has(`POD_MEMBER:${member.pod_member_id}`),
    );
    const peopleNeedle = peopleQuery.trim().toLowerCase();
    const matchingMembers = peopleNeedle
        ? addableMembers.filter((member) => (
            `${member.user_name || ''} ${member.email || ''} ${member.user_email || ''}`
                .toLowerCase()
                .includes(peopleNeedle)
        ))
        : addableMembers;

    const handleOpenChange = (nextOpen: boolean) => {
        if (nextOpen) {
            setView('access');
            setConfirming(null);
            setOptimistic(null);
            setError(null);
            setPeopleQuery('');
        }
        setOpen(nextOpen);
    };

    /**
     * Leaving the pod, and shutting the door behind you, are the two steps here
     * that a member list cannot walk back. They ask once — in the row, so the
     * question and the answer are the same object. Reopening on an already
     * public resource does not re-ask: `current` is what it is compared against.
     */
    const chooseVisibility = (next: ResourceVisibilityValue) => {
        if (next === shownVisibility) {
            setConfirming(null);
            return;
        }
        if (next === 'PUBLIC' && current !== 'PUBLIC') {
            setConfirming('PUBLIC');
            return;
        }
        if (next === 'PERSONAL' && memberGrants.length > 0) {
            setConfirming('PERSONAL');
            return;
        }
        setConfirming(null);
        applyVisibility.mutate(next);
    };

    const commitConfirmed = (next: ResourceVisibilityValue) => {
        setConfirming(null);
        applyVisibility.mutate(next, {
            onSuccess: async () => {
                if (next !== 'PERSONAL' || !canManageSpecificAccess) return;
                // The confirmation said these would go, so they go — and only
                // once the visibility change itself has landed.
                for (const grant of memberGrants) {
                    await applyGrant
                        .mutateAsync({
                            kind: 'remove',
                            key: grantKey(grant),
                            granteeType: grant.grantee_type,
                            granteeId: grant.grantee_id,
                        })
                        .catch(() => null);
                }
            },
        });
    };

    const addPersonGrant = (member: PodMemberResponse) => {
        const level = accessLevels[0];
        if (!level) return;
        applyGrant.mutate({
            kind: 'set',
            key: `POD_MEMBER:${member.pod_member_id}`,
            granteeType: 'POD_MEMBER' as GranteeType,
            granteeId: member.pod_member_id,
            permissionIds: level.permissionIds,
        });
        setPeopleQuery('');
    };

    const confirmCopy = confirming === 'PUBLIC'
        ? optionCopies.find((option) => option.value === 'PUBLIC')?.description
            ?? 'Anyone with a Lemma account will be able to open it.'
        : `Only you will be able to open it. ${memberGrants.length} ${memberGrants.length === 1 ? 'person' : 'people'} with direct access will lose it.`;
    const confirmAction = confirming === 'PUBLIC'
        ? (optionCopies.find((option) => option.value === 'PUBLIC')?.label ?? 'Make public')
        : 'Only me';

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

    return (
        // `modal`, because four call sites open this from inside a dropdown
        // menu. A non-modal popover leaves the menu's own dismiss layer live, so
        // the first click *into* the panel reads as a click outside the menu:
        // the menu closes, taking this whole component down with it. The dialog
        // this replaced was modal and never had the problem.
        <Popover open={open} onOpenChange={handleOpenChange} modal>
            {/* Anchor rather than Trigger, and a real wrapper rather than
                `asChild`, for two different reasons.

                Not Trigger: the caller's node already owns its onClick, and
                Trigger would add a second, competing one.

                Not `asChild`: `trigger` returns whatever the caller wants, and
                two of them return a `<Tooltip>` — a Radix root that renders no
                host element. Slot hands it a ref it has nowhere to put, so the
                popover ends up with no anchor and paints nothing at all.

                Which leaves a wrapper, whose one rule is that it must have a
                box. Four call sites used to pass `className="contents"` to
                suppress exactly that, which is what put the panel in the corner
                of the screen: a box is what Radix measures. So the wrapper takes
                no class from the caller, and those four no longer pass one. */}
            <PopoverAnchor>{triggerNode}</PopoverAnchor>

            <PopoverContent align="end" sideOffset={8} className="w-[23rem] overflow-hidden p-0">
                {view === 'people' ? (
                    <Command shouldFilter={false} className="h-auto overflow-visible rounded-none border-0 bg-transparent shadow-none">
                        <ShareViewHeader title="People with access" onBack={() => setView('access')}>
                            <CommandInput
                                value={peopleQuery}
                                onValueChange={setPeopleQuery}
                                placeholder="Search people"
                                className="h-9"
                                autoFocus
                            />
                        </ShareViewHeader>
                        <div className="max-h-[19rem] overflow-y-auto p-1">
                            {grants.length > 0 ? (
                                <>
                                    <p className="px-2 pb-1 pt-1.5 type-eyebrow">With access</p>
                                    <ul className="space-y-0.5">
                                        {grants.map((grant) => (
                                            <li key={grantKey(grant)} className="flex items-center gap-2 rounded-md px-2 py-1.5">
                                                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--surface-3)] text-xs text-[var(--text-secondary)]">
                                                    {grant.grantee_type === 'ROLE'
                                                        ? <UsersRound className="h-3.5 w-3.5" />
                                                        : getGrantInitials(grant)}
                                                </span>
                                                <span className="min-w-0 flex-1">
                                                    <span className="block truncate text-sm text-[var(--text-primary)]">
                                                        {getGrantLabel(grant)}
                                                    </span>
                                                    <span className="block truncate text-xs text-[var(--text-tertiary)]">
                                                        {grant.grantee_type === 'ROLE'
                                                            ? 'Role'
                                                            : grant.email || getAccessLabel(resourceType!, grant.permission_ids || [])}
                                                    </span>
                                                </span>
                                                {grant.grantee_type === 'ROLE' ? null : (
                                                    <Select
                                                        value={accessLevels.find((level) => samePermissions(level.permissionIds, grant.permission_ids || []))?.value || ''}
                                                        onValueChange={(next) => {
                                                            const level = accessLevels.find((candidate) => candidate.value === next);
                                                            if (!level) return;
                                                            applyGrant.mutate({
                                                                kind: 'set',
                                                                key: grantKey(grant),
                                                                granteeType: grant.grantee_type,
                                                                granteeId: grant.grantee_id,
                                                                permissionIds: level.permissionIds,
                                                            });
                                                        }}
                                                    >
                                                        <SelectTrigger className="h-7 w-[5.5rem] shrink-0 text-xs">
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
                                                )}
                                                <Button
                                                    type="button"
                                                    variant="quiet"
                                                    size="icon"
                                                    className="h-7 w-7 shrink-0"
                                                    onClick={() => applyGrant.mutate({
                                                        kind: 'remove',
                                                        key: grantKey(grant),
                                                        granteeType: grant.grantee_type,
                                                        granteeId: grant.grantee_id,
                                                    })}
                                                    loading={pendingGrantKey === grantKey(grant)}
                                                    aria-label={`Remove ${getGrantLabel(grant)}`}
                                                >
                                                    <Trash2 className="h-3.5 w-3.5" />
                                                </Button>
                                            </li>
                                        ))}
                                    </ul>
                                </>
                            ) : null}

                            <p className="px-2 pb-1 pt-2 type-eyebrow">Add</p>
                            {matchingMembers.length === 0 ? (
                                <p className="px-2 py-2 text-xs text-[var(--text-tertiary)]">
                                    {peopleNeedle
                                        ? 'Nobody in this pod by that name.'
                                        : 'Everyone in this pod already has access.'}
                                </p>
                            ) : (
                                <ul className="space-y-0.5">
                                    {matchingMembers.map((member) => (
                                        <li key={member.pod_member_id}>
                                            <button
                                                type="button"
                                                onClick={() => addPersonGrant(member)}
                                                className="resource-share-nav-button flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none"
                                            >
                                                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--surface-3)] text-xs text-[var(--text-secondary)]">
                                                    <UserRound className="h-3.5 w-3.5" />
                                                </span>
                                                <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]">
                                                    {member.user_name || member.email || member.user_email}
                                                </span>
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    </Command>
                ) : view === 'card' && canShowCard && cardVariant ? (
                    <>
                        <ShareViewHeader title="Share card" onBack={() => setView('access')} />
                        <div className="max-h-[21rem] overflow-y-auto p-3">
                            <SocialCardPanel
                                variant={cardVariant}
                                name={resourceName}
                                url={outsidePodShareUrl}
                                unfurls
                            />
                        </div>
                    </>
                ) : (
                    <>
                        <div className="space-y-1.5 border-b border-[color:var(--border-subtle)] p-2">
                            <ShareLinkRow
                                url={linkToShare}
                                name={resourceName}
                                allowNativeShare={shownVisibility === 'PUBLIC'}
                                emptyHint="A link is available once this is created."
                            />
                            {canShowCard ? (
                                <ShareNavRow label="Share card" onClick={() => setView('card')} />
                            ) : null}
                        </div>

                        <div className="p-1">
                            <p className="flex items-center gap-1.5 px-2 pb-1 pt-1.5 type-eyebrow">
                                General access
                                <ConceptHint concept="grant" />
                            </p>
                            <div className="space-y-0.5">
                                {optionCopies.map((option) => (
                                    <VisibilityOption
                                        key={option.value}
                                        copy={option}
                                        selected={shownVisibility === option.value}
                                        saving={savingVisibility === option.value}
                                        onSelect={() => chooseVisibility(option.value)}
                                    >
                                        {confirming === option.value ? (
                                            <div className="px-2 pb-2">
                                                <p className="text-xs text-[var(--text-secondary)]">{confirmCopy}</p>
                                                <div className="mt-2 flex justify-end gap-1.5">
                                                    <Button
                                                        type="button"
                                                        variant="quiet"
                                                        size="xs"
                                                        onClick={() => setConfirming(null)}
                                                    >
                                                        Not now
                                                    </Button>
                                                    <Button
                                                        type="button"
                                                        variant="primary"
                                                        size="xs"
                                                        onClick={() => commitConfirmed(option.value)}
                                                    >
                                                        {confirmAction}
                                                    </Button>
                                                </div>
                                            </div>
                                        ) : null}
                                    </VisibilityOption>
                                ))}
                            </div>
                        </div>

                        {canManageSpecificAccess && directAccessEnabled ? (
                            <div className="border-t border-[color:var(--border-subtle)] p-1">
                                <ShareNavRow
                                    label={shownVisibility === 'RESTRICTED' ? 'People with access' : 'Additional people'}
                                    meta={isAccessLoading ? '…' : String(grants.length)}
                                    onClick={() => setView('people')}
                                />
                            </div>
                        ) : null}

                        {shownVisibility === 'RESTRICTED' && !isAccessLoading && grants.length === 0 ? (
                            <p className="border-t border-[color:var(--border-subtle)] px-3 py-2 text-xs text-[var(--state-warning)]">
                                No one can open this yet — add the people who need it.
                            </p>
                        ) : null}

                        {error ? (
                            <p className="border-t border-[color:var(--border-subtle)] px-3 py-2 text-xs text-[var(--state-error)]">
                                {error}
                            </p>
                        ) : null}
                    </>
                )}
            </PopoverContent>
        </Popover>
    );
}

export const ResourceVisibilitySelect = ResourceShareButton;
