'use client';

import { use, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowRight, ExternalLink, PanelsTopLeft, Plus, Share2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { ConceptHint } from '@/components/education/concept-hint';
import { SectionPrimer } from '@/components/education/section-primer';
import { ResourceHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { RecipeCard } from '@/components/recipes/recipe-card';
import { ResourceCover } from '@/components/shared/resource-identity';
import { identityGenes } from '@/lib/identity/seeded-identity';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { ResourceShareButton, ResourceVisibilityBadge, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { formatRelativeTime } from '@/components/pod/recent-conversations';
import { Button } from '@/components/ui/button';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { useDeleteApp, useAppPages, useUpdateAppVisibility } from '@/lib/hooks/use-app';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { buildResourceCreationHref } from '@/lib/pods/resource-creation';
import { appRecipes } from '@/lib/recipes/recipes';
import { useLaunchRecipe } from '@/lib/recipes/use-launch-recipe';
import type { AppPageRef } from '@/lib/types/app';
import { StepLoader } from '@/components/brand/loader';

function formatDisplayName(value: string | null | undefined) {
    const cleaned = (value || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!cleaned) return 'Untitled';
    return cleaned.split(' ').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function buildAppViewHref(podId: string, page: string, searchParams: { toString(): string }) {
    const nextParams = new URLSearchParams(searchParams.toString());
    nextParams.set('page', page);
    const query = nextParams.toString();
    return `/pod/${podId}/app/view${query ? `?${query}` : ''}`;
}

export default function AppPagesRoute({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const router = useRouter();
    const searchParams = useSearchParams();
    const podAccess = usePodAccess(podId);
    const canCreateApp = podAccess.can('app.create');
    const canUpdateApp = podAccess.can('app.update');
    const canDeleteApp = podAccess.can('app.delete');
    const { pages, isLoading } = useAppPages(podId);
    const { mutate: deleteApp, isPending: isDeletingApp } = useDeleteApp();
    const { mutateAsync: updateAppVisibility } = useUpdateAppVisibility();
    const { launchRecipe } = useLaunchRecipe(podId);
    const [appPendingDelete, setAppPendingDelete] = useState<AppPageRef | null>(null);

    useEffect(() => {
        const page = searchParams.get('page');
        if (!page) return;
        router.replace(buildAppViewHref(podId, page, searchParams));
    }, [podId, router, searchParams]);

    if (searchParams.get('page')) return null;

    if (isLoading) {
        return (
            <div className="flex h-full items-center justify-center">
                <StepLoader size="sm" />
            </div>
        );
    }

    const createAppWithAssistant = () => {
        if (!canCreateApp) return;

        router.push(buildResourceCreationHref({ podId, kind: 'app', source: 'apps_page' }));
    };

    const handleDeleteApp = () => {
        if (!appPendingDelete) return;
        if (!resourceAllows(appPendingDelete, 'app.delete', canDeleteApp)) return;
        const appName = appPendingDelete.appName || appPendingDelete.title;

        deleteApp(
            { podId, name: appName },
            {
                onSuccess: () => {
                    toast.success('App deleted');
                    setAppPendingDelete(null);
                },
                onError: () => toast.error('Failed to delete app'),
            }
        );
    };

    return (
        <ResourceIndexShell>
            <ResourceHeader
                title="Apps"
                meta={<ConceptHint concept="app" />}
                actions={(
                    canCreateApp ? (
                        // design.md §8: the apps are what this page is for. The
                        // header create stays secondary; the empty state below
                        // is where making one is the only thing to do.
                        <Button variant="secondary"
                            type="button"
                            onClick={() => {
                                void createAppWithAssistant();
                            }}
                            className="h-9 w-fit gap-2 rounded-md px-3 text-sm"
                        >
                            <Plus className="h-4 w-4" />
                            New app
                        </Button>
                    ) : null
                )}
            />

            <SectionPrimer concept="app" className="mb-4" />

            {pages.length === 0 ? (
                canCreateApp ? (
                    <div className="grid gap-5">
                        <div className="max-w-2xl">
                            <h2 className="text-lg font-medium text-[var(--text-primary)]">Choose an app shape</h2>
                            <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                                Start from a recognizable product shape. Lemma builds the app, its data, and the agents or workflows that make it useful.
                            </p>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                            {appRecipes.slice(0, 5).map((recipe) => (
                                <RecipeCard
                                    key={recipe.id}
                                    recipe={recipe}
                                    onLaunch={() => launchRecipe(recipe)}
                                />
                            ))}
                            <button
                                type="button"
                                onClick={createAppWithAssistant}
                                className="resource-index-card resource-option-button custom-focus-ring group flex min-h-[7.5rem] flex-col items-start justify-center gap-2 rounded-lg border border-dashed p-4 text-left transition-colors hover:border-[var(--border-strong)]"
                            >
                                <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]">
                                    <Plus className="h-4 w-4" />
                                </span>
                                <span className="text-sm font-medium text-[var(--text-primary)]">Describe your own</span>
                                <span className="text-xs leading-5 text-[var(--text-tertiary)]">Open a conversation and tell the assistant what this app should help people do.</span>
                            </button>
                        </div>
                    </div>
                ) : (
                    <EmptyState
                        variant="region"
                        icon={<PanelsTopLeft className="h-5 w-5" />}
                        title="No apps yet"
                        description="Build a screen where your team works with the pod's agents — drafts, reviews, and decisions in one place."
                    />
                )
            ) : (
                <section className="resource-index-grid resource-index-grid-md-2 resource-index-grid-xl-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                    {pages.map((page) => {
                        const title = formatDisplayName(page.title || page.slug);
                        const viewHref = buildAppViewHref(podId, page.slug, searchParams);
                        const canShareApp = resourceAllows(page, 'app.update', canUpdateApp);
                        const canDeleteThisApp = resourceAllows(page, 'app.delete', canDeleteApp);
                        // One hue per card, taken from the same seed the cover is
                        // drawn from. This used to be `getAppAccent(page.slug)`, a
                        // second independent hash, which meant the monogram and the
                        // cover routinely disagreed — a green badge on a pink cover.
                        const hue = identityGenes(page.slug).tone;
                        const appName = page.appName || page.title;
                        const isReady = (page.status || '').toUpperCase() === 'READY';
                        const updatedLabel = formatRelativeTime(page.updatedAt);
                        const appShareUrl = typeof window === 'undefined'
                            ? undefined
                            : `${window.location.origin}${viewHref}`;
                        const hasMenuActions = canShareApp || Boolean(page.url) || canDeleteThisApp;

                        return (
                            <article
                                key={page.slug}
                                className={`resource-index-card app-tile lm-identity-hue-${hue} group relative overflow-hidden`}
                            >
                                {/* Mouse affordance only: the whole card is clickable, but
                                    keyboard and screen readers get the labelled controls in
                                    the footer instead of a second link to the same href. */}
                                <Link href={viewHref} aria-hidden tabIndex={-1} className="app-tile-hit" />

                                {/* An app is the one resource that *is* a screen, and the card
                                    used to show none of it — a monogram in a box above three
                                    lines of text. Until real thumbnails exist, a seeded
                                    abstraction of a layout at least gives every app in the
                                    grid its own silhouette. */}
                                <div className="app-tile-cover">
                                    <ResourceCover seed={page.slug} />
                                    {/* An app is READY from its first bundle upload onward, so
                                        "live" is true of nearly every card and says nothing.
                                        Only the exception — not yet deployed — is worth a badge. */}
                                    {!isReady ? (
                                        <span className="app-tile-status">
                                            <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                                            Draft
                                        </span>
                                    ) : null}
                                </div>

                                <div className="app-tile-body p-4">
                                    <span className="app-icon app-tile-mark flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-base font-medium">
                                        {page.icon || title.charAt(0)}
                                    </span>

                                    <div className="mt-2.5 min-w-0">
                                        <h2 className="resource-index-card-title truncate text-base font-medium text-[var(--text-primary)]">{title}</h2>
                                        {page.description ? (
                                            <p className="resource-index-card-summary mt-1 line-clamp-2 text-[var(--text-secondary)]">
                                                {page.description}
                                            </p>
                                        ) : null}
                                    </div>

                                    <div className="mt-3 flex items-center justify-between gap-2 text-xs text-[var(--text-tertiary)]">
                                    <div className="flex min-w-0 items-center gap-3">
                                        <ResourceVisibilityBadge visibility={page.visibility} resourceLabel="apps" resourceType="app" hideWhenDefault />
                                        {updatedLabel ? <span className="truncate">Updated {updatedLabel}</span> : null}
                                    </div>
                                    <div className="flex shrink-0 items-center gap-1">
                                        <Link href={viewHref} aria-label={`Open ${title}`} title="Open" className="app-tile-action app-tile-control">
                                            <ArrowRight className="h-4 w-4" />
                                        </Link>
                                        {page.url ? (
                                            <a
                                                href={page.url}
                                                target="_blank"
                                                rel="noreferrer"
                                                aria-label={`Open ${title} in a new tab`}
                                                title="Open in new tab"
                                                className="app-tile-action app-tile-control"
                                            >
                                                <ExternalLink className="h-4 w-4" />
                                            </a>
                                        ) : null}
                                    {hasMenuActions ? (
                                        <ResourceActionsMenu
                                            ariaLabel={`Open actions for ${title}`}
                                            triggerClassName="app-tile-control h-7 w-7 -mr-1 -mt-1 opacity-60 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                                        >
                                            {canShareApp ? (
                                                <ResourceShareButton
                                                    value={page.visibility}
                                                    podId={podId}
                                                    resourceType="app"
                                                    resourceId={page.id}
                                                    resourceLabel="apps"
                                                    resourceName={title}
                                                    shareUrl={appShareUrl}
                                                    disabled={!page.id || !appName}
                                                    onChange={async (visibility: ResourceVisibilityValue) => {
                                                        await updateAppVisibility({ podId, name: appName, visibility });
                                                    }}
                                                    className="contents"
                                                    trigger={({ openShare, disabled }) => (
                                                        <DropdownMenuItem
                                                            disabled={disabled}
                                                            onSelect={(event) => {
                                                                event.preventDefault();
                                                                openShare();
                                                            }}
                                                        >
                                                            <Share2 className="mr-2 h-4 w-4" />
                                                            Share
                                                        </DropdownMenuItem>
                                                    )}
                                                />
                                            ) : null}
                                            {page.url ? (
                                                <DropdownMenuItem asChild>
                                                    <a href={page.url} target="_blank" rel="noreferrer">
                                                        <ExternalLink className="mr-2 h-4 w-4" />
                                                        Open live app
                                                    </a>
                                                </DropdownMenuItem>
                                            ) : null}
                                            {canDeleteThisApp ? (
                                                <DestructiveResourceActionItem onSelect={() => setAppPendingDelete(page)}>
                                                    Delete app
                                                </DestructiveResourceActionItem>
                                            ) : null}
                                        </ResourceActionsMenu>
                                    ) : null}
                                    </div>
                                    </div>
                                </div>
                            </article>
                        );
                    })}
                </section>
            )}
            <DestructiveConfirmationDialog
                open={Boolean(appPendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setAppPendingDelete(null);
                }}
                title="Delete app"
                description={`Delete "${appPendingDelete ? formatDisplayName(appPendingDelete.title || appPendingDelete.slug) : 'this app'}"? This removes the app from this pod.`}
                resourceName={appPendingDelete ? formatDisplayName(appPendingDelete.title || appPendingDelete.slug) : ''}
                consequences={[
                    'People using this app will no longer be able to open its app surface.',
                    'Any deployed app bundle and app-specific assets will be removed.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete app"
                pendingLabel="Deleting app..."
                isPending={isDeletingApp}
                onConfirm={handleDeleteApp}
            />
        </ResourceIndexShell>
    );
}
