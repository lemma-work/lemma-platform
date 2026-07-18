'use client';

import { use, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowUpRight, ExternalLink, Loader2, PanelsTopLeft, Plus, Share2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { StepLoader } from '@/components/brand/loader';
import { ConceptHint } from '@/components/education/concept-hint';
import { SectionPrimer } from '@/components/education/section-primer';
import { ResourceIndexHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { ResourceShareButton, ResourceVisibilityBadge, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { APP_ACCENTS } from '@/lib/app/app-accent';
import { Button } from '@/components/ui/button';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { useDeleteApp, useAppPages, useUpdateAppVisibility } from '@/lib/hooks/use-app';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { appRecipes, getRecipeAccent, type Recipe } from '@/lib/recipes/recipes';
import { renderRecipeIcon } from '@/components/recipes/recipe-icon';
import { useLaunchRecipe } from '@/lib/recipes/use-launch-recipe';
import type { AppPageRef } from '@/lib/types/app';

function formatDisplayName(value: string | null | undefined) {
    const cleaned = (value || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!cleaned) return 'Untitled';
    return cleaned.split(' ').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function formatAppHost(value: string | null | undefined) {
    if (!value) return 'Ready';
    try {
        return new URL(value).hostname.replace(/^www\./, '') || 'Live app';
    } catch {
        return 'Live app';
    }
}

function RecipeStarterCard({ recipe, onLaunch }: { recipe: Recipe; onLaunch: () => void }) {
    return (
        <button
            type="button"
            onClick={onLaunch}
            className="resource-index-card custom-focus-ring group flex min-h-[7.5rem] flex-col items-start gap-2 rounded-lg p-4 text-left transition-colors hover:border-[var(--border-strong)]"
        >
            <div className="flex w-full items-start justify-between gap-2">
                <span className="recipe-icon-tile h-9 w-9 rounded-lg" data-accent={getRecipeAccent(recipe)}>
                    {renderRecipeIcon(recipe, { className: 'h-[18px] w-[18px]', strokeWidth: 1.8 })}
                </span>
                <span className="inline-flex items-center gap-1 text-xs text-[var(--text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100">
                    Build
                    <ArrowUpRight className="h-3.5 w-3.5" />
                </span>
            </div>
            <span className="text-sm font-medium text-[var(--text-primary)]">{recipe.name}</span>
            <span className="line-clamp-2 text-xs leading-5 text-[var(--text-tertiary)]">{recipe.blurb}</span>
        </button>
    );
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
    const assistant = useAIAssistant();
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

        const params = new URLSearchParams();
        params.set('conversationInstructions', [
            'You are helping create a Lemma app app in the current pod.',
            'Use the user-visible message as the product intent. Do not repeat these hidden instructions back to the user.',
            'Start by understanding the operator workflow, then create a minimal useful Lemma app app with the right data, pages, and interactions.',
            'Keep it minimal, calm, and operational; avoid generic dashboard chrome.',
            'After it is built, summarize what was created and display or link the app.',
        ].join('\n'));
        params.set('conversationMetadata', JSON.stringify({
            source: 'apps_page',
            intent: 'create_resource',
            resource_type: 'app',
        }));

        router.push(`/pod/${podId}/conversations/new?${params.toString()}`);
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
            <ResourceIndexHeader
                title="Apps"
                productIconKind="apps"
                meta={<ConceptHint concept="app" />}
                actions={(
                    canCreateApp ? (
                        <Button
                            type="button"
                            onClick={() => {
                                void createAppWithAssistant();
                            }}
                            disabled={assistant.isLoading || assistant.isOpenedConversationRunning}
                            className="h-9 w-fit gap-2 rounded-md px-3 text-sm"
                        >
                            {assistant.isLoading || assistant.isOpenedConversationRunning ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Plus className="h-4 w-4" />
                            )}
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
                            <h2 className="text-lg font-medium text-[var(--text-primary)]">Start from a recipe</h2>
                            <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                                Pick a starting point and the assistant builds it into a working app — a screen where your team works with this pod’s agents. Or describe your own.
                            </p>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                            {appRecipes.slice(0, 5).map((recipe) => (
                                <RecipeStarterCard
                                    key={recipe.id}
                                    recipe={recipe}
                                    onLaunch={() => launchRecipe(recipe)}
                                />
                            ))}
                            <button
                                type="button"
                                onClick={createAppWithAssistant}
                                className="resource-index-card custom-focus-ring group flex min-h-[7.5rem] flex-col items-start justify-center gap-2 rounded-lg border border-dashed p-4 text-left transition-colors hover:border-[var(--border-strong)]"
                            >
                                <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]">
                                    <Plus className="h-4 w-4" />
                                </span>
                                <span className="text-sm font-medium text-[var(--text-primary)]">Describe your own</span>
                                <span className="text-xs leading-5 text-[var(--text-tertiary)]">Open a conversation and tell the assistant what this app should help people do.</span>
                            </button>
                        </div>
                        <Link
                            href={`/pod/${podId}/recipes`}
                            className="custom-focus-ring inline-flex w-fit items-center gap-1.5 text-sm font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                        >
                            Browse all recipes — bots, creator tools, and more
                            <ArrowUpRight className="h-4 w-4" />
                        </Link>
                    </div>
                ) : (
                    <EmptyState
                        variant="panel"
                        icon={<PanelsTopLeft className="h-5 w-5" />}
                        title="No apps yet"
                        description="Build a screen where your team works with the pod's agents — drafts, reviews, and decisions in one place."
                    />
                )
            ) : (
                <section className="apps-grid">
                    {pages.map((page, index) => {
                        const title = formatDisplayName(page.title || page.slug);
                        const viewHref = buildAppViewHref(podId, page.slug, searchParams);
                        const canShareApp = resourceAllows(page, 'app.update', canUpdateApp);
                        const canDeleteThisApp = resourceAllows(page, 'app.delete', canDeleteApp);
                        // The gallery order is stable, so cycling the semantic palette here
                        // guarantees neighbouring cards remain visually distinct.
                        const accent = APP_ACCENTS[index % APP_ACCENTS.length];
                        const appName = page.appName || page.title;
                        const appHost = formatAppHost(page.url);
                        const appShareUrl = typeof window === 'undefined'
                            ? undefined
                            : `${window.location.origin}${viewHref}`;
                        const hasMenuActions = canShareApp || Boolean(page.url) || canDeleteThisApp;

                        return (
                            <article
                                key={page.slug}
                                data-accent={accent}
                                className="resource-index-card app-tile group relative flex min-h-28 flex-col p-4"
                            >
                                <div className="flex min-w-0 items-start gap-3">
                                    <Link
                                        href={viewHref}
                                        aria-label={`Open ${title}`}
                                        className="custom-focus-ring flex min-w-0 flex-1 items-start gap-3 rounded-lg"
                                    >
                                        <span className="app-icon flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-base font-medium">
                                            {page.icon || title.charAt(0)}
                                        </span>
                                        <span className="min-w-0 flex-1 pt-0.5">
                                            <span className="block truncate text-base font-semibold text-[var(--text-primary)]">
                                                {title}
                                            </span>
                                            {page.description ? (
                                                <span className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--text-secondary)]">
                                                    {page.description}
                                                </span>
                                            ) : null}
                                        </span>
                                    </Link>
                                    {hasMenuActions ? (
                                        <ResourceActionsMenu
                                            ariaLabel={`Open actions for ${title}`}
                                            triggerClassName="h-7 w-7 -mr-1 -mt-1 opacity-60 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
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

                                <Link
                                    href={viewHref}
                                    className="custom-focus-ring mt-auto flex items-center justify-between gap-3 rounded-md pt-3 text-xs text-[var(--text-tertiary)]"
                                >
                                    <span className="flex min-w-0 items-center gap-2">
                                        <span className="inline-flex min-w-0 items-center gap-1.5">
                                            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--state-success)]" aria-hidden />
                                            <span className="truncate">{appHost}</span>
                                        </span>
                                        <ResourceVisibilityBadge visibility={page.visibility} resourceLabel="apps" hideWhenDefault />
                                    </span>
                                    <span className="ml-auto inline-flex shrink-0 items-center gap-1 font-medium text-[var(--text-secondary)] transition-gentle group-hover:translate-x-0.5">
                                        Open
                                        <ArrowUpRight className="h-3.5 w-3.5" />
                                    </span>
                                </Link>
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
                description={`Delete "${appPendingDelete ? formatDisplayName(appPendingDelete.title || appPendingDelete.slug) : 'this app'}"? This removes the app app surface from this pod.`}
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
