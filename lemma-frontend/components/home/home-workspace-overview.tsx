'use client';

import { useMemo, useState, useSyncExternalStore } from 'react';
import Link from 'next/link';
import {
    Copy,
    ExternalLink,
    Plus,
    Search,
    Settings,
    Share2,
} from '@/components/ui/icons';
import { toast } from 'sonner';
import { ShareSheet } from '@/components/bundle/share-sheet';
import { EmptyPodsState } from '@/components/home/empty-pods-state';
import { EmojiPicker } from '@/components/shared/emoji-picker';
import { PodMark } from '@/components/pod/pod-mark';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { Skeleton } from '@/components/shared/loading';
import { Button } from '@/components/ui/button';
import {
    DropdownMenuItem,
    DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { useAppPagesForPods } from '@/lib/hooks/use-app';
import { useDeletePod, useUpdatePod, type AccessiblePod } from '@/lib/hooks/use-pods';
import { readLastOpenedPodId, subscribeToLastOpenedPodId } from '@/lib/pods/last-opened-pod';
import { humanizeName } from '@/lib/utils/display-name';
import { parseResourceIcon } from '@/lib/utils/resource-icon-value';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import type { AppPageRef } from '@/lib/types/app';

/** Apps one pod shows before it starts counting. Past this the shelf stops
 *  being a set of things to open and becomes a second list. */
const MAX_VISIBLE_APP_TILES = 6;

/** Searching a handful of pods is not work. The field only earns its place
 *  once there are enough of them that finding one is a task of its own. */
const SEARCH_THRESHOLD = 4;

/** Each pod's shelf costs one app-index request, so an org with fifty pods
 *  would open the page with fifty of them. Pods past this point still list and
 *  still open — they just arrive without their shelf. */
const MAX_PODS_WITH_APP_SHORTCUTS = 12;

export function HomeWorkspaceOverview({
    pods,
    showOrganizationName,
    showCreateAction,
    isLoading,
    error,
}: {
    pods: AccessiblePod[];
    showOrganizationName?: boolean;
    showCreateAction?: boolean;
    isLoading?: boolean;
    error?: unknown;
}) {
    const { mutate: deletePod, isPending: isDeletingPod } = useDeletePod();
    const [podPendingDelete, setPodPendingDelete] = useState<AccessiblePod | null>(null);
    const [podPendingShare, setPodPendingShare] = useState<AccessiblePod | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const showSearch = pods.length > SEARCH_THRESHOLD;
    // With nothing to list, the header row is a lone "New Pod" hovering above a
    // panel offering the same thing — so the empty state owns the CTA outright
    // and the row stands down. Only once loading has settled: a button that
    // appears after the skeleton reads as a jump.
    const hasNoPods = !isLoading && !error && pods.length === 0;

    const filteredPods = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        if (!query) return pods;

        return pods.filter((pod) => (
            pod.name.toLowerCase().includes(query) ||
            (pod.description || '').toLowerCase().includes(query) ||
            (pod.organization_name || '').toLowerCase().includes(query)
        ));
    }, [pods, searchQuery]);

    // The pod the root route would have sent them to. It leads for the same
    // reason it wins that redirect: it is almost always the one they want. At
    // three pods that is worth an ordering, not a "Resume" heading over one of
    // them.
    const lastOpenedPodId = useSyncExternalStore(
        subscribeToLastOpenedPodId,
        readLastOpenedPodId,
        () => null,
    );
    const orderedPods = useMemo(() => {
        if (!lastOpenedPodId) return filteredPods;

        const lead = filteredPods.filter((pod) => pod.id === lastOpenedPodId);
        if (lead.length === 0) return filteredPods;
        return [...lead, ...filteredPods.filter((pod) => pod.id !== lastOpenedPodId)];
    }, [filteredPods, lastOpenedPodId]);

    const podIdsWithShortcuts = useMemo(
        () => orderedPods.slice(0, MAX_PODS_WITH_APP_SHORTCUTS).map((pod) => pod.id),
        [orderedPods],
    );
    const appPagesByPod = useAppPagesForPods(podIdsWithShortcuts);

    const handleDeletePod = () => {
        if (!podPendingDelete) return;

        deletePod(podPendingDelete.id, {
            onSuccess: () => {
                toast.success('Pod deleted');
                setPodPendingDelete(null);
            },
            onError: () => toast.error('Failed to delete pod'),
        });
    };

    return (
        // A measure, not a shell width. Three pods stretched across 1150px is
        // most of why this page read as empty.
        <div className="mx-auto w-full max-w-3xl">
            <section>
                {!hasNoPods && (showCreateAction || showSearch) ? (
                    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        {showSearch ? (
                            <div className="relative w-full sm:max-w-xs">
                                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
                                <input
                                    type="search"
                                    value={searchQuery}
                                    onChange={(event) => setSearchQuery(event.target.value)}
                                    placeholder="Search pods"
                                    className="form-field-control h-10 w-full pl-9 pr-3 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)] focus-ring"
                                />
                            </div>
                        ) : (
                            <span />
                        )}
                        {showCreateAction ? (
                            <Button variant="primary" asChild size="sm" className="w-full gap-2 px-4 sm:w-auto">
                                <Link href="/create-pod">
                                    <Plus className="h-4 w-4" />
                                    New Pod
                                </Link>
                            </Button>
                        ) : null}
                    </div>
                ) : null}

                {error ? (
                    <div className="surface-panel-muted px-4 py-4 text-sm text-[var(--state-error)]">
                        Failed to load pods.
                    </div>
                ) : isLoading ? (
                    <div>
                        {Array.from({ length: 2 }).map((_, index) => (
                            <div key={index} className="home-pod-item">
                                <div className="flex items-start gap-3">
                                    <Skeleton shape="block" className="h-11 w-11 rounded-lg" />
                                    <div className="min-w-0 flex-1 space-y-2">
                                        <Skeleton shape="block" className="h-4 w-44 max-w-full" />
                                        <Skeleton className="h-3 w-72 max-w-full" />
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                ) : orderedPods.length > 0 ? (
                    <div>
                        {orderedPods.map((pod) => (
                            <PodItem
                                key={pod.id}
                                pod={pod}
                                appPages={appPagesByPod.get(pod.id) || []}
                                showOrganizationName={showOrganizationName}
                                canManage={showCreateAction}
                                onShare={() => setPodPendingShare(pod)}
                                onDelete={() => setPodPendingDelete(pod)}
                            />
                        ))}
                    </div>
                ) : pods.length > 0 ? (
                    <div className="surface-panel-muted px-4 py-4 text-sm text-[var(--text-secondary)]">
                        No pods match that search.
                    </div>
                ) : (
                    <EmptyPodsState />
                )}
            </section>

            {podPendingShare ? (
                <ShareSheet
                    podId={podPendingShare.id}
                    podName={podPendingShare.name}
                    open
                    onOpenChange={(open) => {
                        if (!open) setPodPendingShare(null);
                    }}
                />
            ) : null}

            <DestructiveConfirmationDialog
                open={Boolean(podPendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setPodPendingDelete(null);
                }}
                title="Delete pod"
                description={`Delete "${podPendingDelete?.name ?? 'this pod'}"? This removes the workspace and its operating surfaces.`}
                resourceName={podPendingDelete?.name ?? ''}
                consequences={[
                    'Apps, agents, workflows, schedules, tables, docs, and pod context inside this pod will be removed.',
                    'People with access will no longer be able to open this workspace.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete pod"
                pendingLabel="Deleting pod..."
                isPending={isDeletingPod}
                onConfirm={handleDeletePod}
            />
        </div>
    );
}

/**
 * A pod's app, one click away.
 *
 * The link goes to the app's own URL in a new tab, not to the in-pod viewer:
 * from here there is no pod shell to frame it with, so routing through the
 * workspace would boot the whole shell to end up showing the same iframe. Pages
 * that carry no URL of their own still fall back to the viewer, which is the
 * only place they can be seen.
 */
function AppShortcut({ pod, page }: { pod: AccessiblePod; page: AppPageRef }) {
    // Apps are named as slugs because a bundle and a URL both need one. The
    // shelf is the one place that name is read rather than resolved.
    const title = humanizeName(page.title);

    // One glyph, and it is the one carrying information. Sitting on the shelf
    // under a pod already says "app"; what the row cannot otherwise tell you is
    // that the click leaves the page.
    const label = (
        <>
            <span className="truncate">{title}</span>
            {/* No colour of its own, so it travels with the chip's hover
                rather than staying grey while the label goes violet. */}
            {page.url ? <ExternalLink className="h-3.5 w-3.5 shrink-0 opacity-70" /> : null}
        </>
    );

    if (!page.url) {
        return (
            <Link
                href={`/pod/${pod.id}/app/view?page=${encodeURIComponent(page.slug)}`}
                className="home-pod-app-tile custom-focus-ring"
                title={`Open ${title} in ${pod.name}`}
            >
                {label}
            </Link>
        );
    }

    return (
        <a
            href={page.url}
            target="_blank"
            rel="noreferrer"
            className="home-pod-app-tile custom-focus-ring"
            title={`Open ${title} in a new tab`}
        >
            {label}
        </a>
    );
}

function PodActions({
    pod,
    canManage,
    onShare,
    onDelete,
}: {
    pod: AccessiblePod;
    canManage?: boolean;
    onShare: () => void;
    onDelete: () => void;
}) {
    const copyLink = () => {
        const href = `${window.location.origin}/pod/${pod.id}`;
        void navigator.clipboard
            .writeText(href)
            .then(() => toast.success('Link copied'))
            .catch(() => toast.error('Could not copy the link'));
    };

    return (
        <div className="home-pod-item-actions">
            {canManage ? (
                <button
                    type="button"
                    onClick={onShare}
                    aria-label={`Share ${pod.name}`}
                    title="Share pod"
                    className="lemma-quiet-icon-button custom-focus-ring h-8 w-8"
                >
                    <Share2 className="h-4 w-4" />
                </button>
            ) : null}
            <ResourceActionsMenu ariaLabel={`Open actions for ${pod.name}`} triggerClassName="h-8 w-8">
                <DropdownMenuItem onSelect={copyLink}>
                    <Copy className="mr-2 h-4 w-4" />
                    Copy link
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                    <Link href={`/pod/${pod.id}/settings`}>
                        <Settings className="mr-2 h-4 w-4" />
                        Pod settings
                    </Link>
                </DropdownMenuItem>
                {canManage ? (
                    <DropdownMenuItem
                        onSelect={(event) => {
                            event.preventDefault();
                            onShare();
                        }}
                    >
                        <Share2 className="mr-2 h-4 w-4" />
                        Share pod
                    </DropdownMenuItem>
                ) : null}
                <DropdownMenuSeparator />
                <DestructiveResourceActionItem onSelect={onDelete}>
                    Delete pod
                </DestructiveResourceActionItem>
            </ResourceActionsMenu>
        </div>
    );
}

function PodItem({
    pod,
    appPages,
    showOrganizationName,
    canManage,
    onShare,
    onDelete,
}: {
    pod: AccessiblePod;
    appPages: AppPageRef[];
    showOrganizationName?: boolean;
    canManage?: boolean;
    onShare: () => void;
    onDelete: () => void;
}) {
    const updatePod = useUpdatePod();
    const visibleApps = appPages.slice(0, MAX_VISIBLE_APP_TILES);
    const hiddenAppCount = appPages.length - visibleApps.length;

    // `updated_at` is when the pod record changed, not when work last ran in it,
    // so this says exactly that rather than dressing it up as activity.
    const updatedAgo = formatRelativeTime(pod.updated_at);
    const secondaryLine = pod.description?.trim() || (updatedAgo ? `Updated ${updatedAgo}` : null);

    const storedIcon = parseResourceIcon(pod.icon_url);
    const storedGlyph = storedIcon?.kind === 'glyph' ? storedIcon.glyph : null;

    const commitIcon = (nextIcon: string | null) => {
        updatePod.mutate(
            { id: pod.id, data: { icon_url: nextIcon } },
            {
                onSuccess: () => toast.success(nextIcon ? 'Pod icon updated' : 'Pod icon cleared'),
                onError: (error) => toast.error(`Failed to update icon: ${error.message}`),
            },
        );
    };

    // The same mark the pod switcher wears — a pod is violet wherever the
    // product draws one.
    const mark = (
        <ResourceIcon
            iconUrl={pod.icon_url}
            alt={`${pod.name} icon`}
            label={pod.name}
            identityKind="team"
            identitySeed={pod.id}
            identitySize={44}
            className="h-11 w-11 shrink-0 rounded-lg bg-transparent text-[var(--text-tertiary)]"
            fallback={<PodMark name={pod.name} size="lg" />}
        />
    );

    return (
        <article className="home-pod-item group">
            <div className="flex items-start gap-4">
                {/* The mark is the one part of the row that is not a way into
                    the pod: it is the pod's face, and this is where you are
                    already looking at it, so it is where you change it. */}
                {canManage ? (
                    <EmojiPicker
                        value={storedGlyph}
                        onSelect={(glyph) => commitIcon(glyph)}
                        onClear={() => commitIcon(null)}
                        disabled={updatePod.isPending}
                    >
                        <Button
                            variant="quiet"
                            size="icon"
                            disabled={updatePod.isPending}
                            aria-label={`Change the icon for ${pod.name}`}
                            className="h-11 w-11 shrink-0 rounded-lg p-0"
                        >
                            {mark}
                        </Button>
                    </EmojiPicker>
                ) : (
                    <Link
                        href={`/pod/${pod.id}`}
                        className="custom-focus-ring shrink-0 rounded-lg"
                        aria-label={`Open ${pod.name}`}
                    >
                        {mark}
                    </Link>
                )}
                <Link
                    href={`/pod/${pod.id}`}
                    className="custom-focus-ring flex min-w-0 flex-1 items-start gap-4 rounded-lg"
                >
                    <span className="min-w-0 flex-1">
                        <span className="flex min-w-0 items-baseline gap-2">
                            <span className="truncate text-base font-medium text-[var(--text-primary)]">
                                {pod.name}
                            </span>
                            {showOrganizationName && pod.organization_name ? (
                                <span className="shrink-0 truncate text-xs text-[var(--text-tertiary)]">
                                    {pod.organization_name}
                                </span>
                            ) : null}
                        </span>
                        {/* One line. A pod's own description can run to a
                            paragraph, and four of them stacked was most of what
                            made this page loud — here it is a label, not the
                            document. */}
                        {secondaryLine ? (
                            <span className="mt-1 block truncate text-sm leading-6 text-[var(--text-secondary)]">
                                {secondaryLine}
                            </span>
                        ) : null}
                    </span>
                </Link>
                <PodActions pod={pod} canManage={canManage} onShare={onShare} onDelete={onDelete} />
            </div>

            {visibleApps.length > 0 ? (
                <div className="home-pod-apps">
                    {visibleApps.map((page) => (
                        <AppShortcut key={page.slug} pod={pod} page={page} />
                    ))}
                    {hiddenAppCount > 0 ? (
                        <Link
                            href={`/pod/${pod.id}/app/pages`}
                            className="home-pod-app-tile custom-focus-ring"
                            title={`All apps in ${pod.name}`}
                        >
                            +{hiddenAppCount} more
                        </Link>
                    ) : null}
                </div>
            ) : null}
        </article>
    );
}
