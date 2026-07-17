'use client';

import { use, type ReactNode } from 'react';
import Link from 'next/link';
import { ArrowLeft, Check, ChevronDown, ExternalLink, PlayCircle, Sparkles } from '@/components/ui/icons';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { RecipeReadme } from '@/components/recipes/recipe-readme';
import { renderRecipeIcon } from '@/components/recipes/recipe-icon';
import { ResourceIndexHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { Button } from '@/components/ui/button';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import {
    RECIPE_BUILDS_LABEL,
    RECIPE_CATEGORIES,
    getRecipeAccent,
    getRecipeById,
    getRecipeHighlights,
    recipeToKit,
    type Recipe,
} from '@/lib/recipes/recipes';
import { useLaunchRecipe } from '@/lib/recipes/use-launch-recipe';

function formatPodName(value: string | null | undefined) {
    const cleaned = (value || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!cleaned) return 'this pod';
    return cleaned.split(' ').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

export default function RecipeDetailPage({ params }: { params: Promise<{ id: string; recipeId: string }> }) {
    const { id: podId, recipeId } = use(params);
    const recipe = getRecipeById(recipeId);

    return (
        <ProtectedRoute>
            {recipe ? (
                <RecipeDetail podId={podId} recipe={recipe} />
            ) : (
                <ResourceIndexShell>
                    <ResourceIndexHeader
                        title="Recipe not found"
                        productIconKind="apps"
                        backHref={`/pod/${podId}/recipes`}
                        backLabel="Recipes"
                    />
                    <div className="surface-panel p-6">
                        <Button asChild variant="outline">
                            <Link href={`/pod/${podId}/recipes`}>
                                <ArrowLeft className="mr-2 h-4 w-4" />
                                Back to recipes
                            </Link>
                        </Button>
                    </div>
                </ResourceIndexShell>
            )}
        </ProtectedRoute>
    );
}

function RecipeDetail({ podId, recipe }: { podId: string; recipe: Recipe }) {
    const { data: pod } = usePod(podId);
    const podName = formatPodName(pod?.name);
    const podAccess = usePodAccess(podId);
    const canBuild = podAccess.can('conversation.write');
    const { launchRecipe } = useLaunchRecipe(podId, { podName });

    const isPrompt = recipe.source.kind === 'prompt';
    const kit = recipeToKit(recipe);
    const accent = getRecipeAccent(recipe);
    const highlights = getRecipeHighlights(recipe);
    const categoryLabel = RECIPE_CATEGORIES.find((category) => category.id === recipe.category)?.label;

    const helper = isPrompt
        ? 'Opens a conversation where the assistant builds this with you. You stay in control of every step.'
        : 'Opens a conversation where the assistant installs this kit from its source. You approve before anything is created.';

    return (
        <ResourceIndexShell>
            <ResourceIndexHeader
                title={recipe.name}
                productIconKind="apps"
                backHref={`/pod/${podId}/recipes`}
                backLabel="Recipes"
                actions={recipe.source.kind === 'repo' ? (
                    <a
                        href={recipe.source.github}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex h-8 items-center gap-2 rounded-md border border-[color:var(--button-secondary-border)] bg-[var(--button-secondary-bg)] px-2.5 text-sm font-medium text-[var(--button-secondary-fg)] transition-colors hover:bg-[var(--button-secondary-bg-hover)]"
                    >
                        Source
                        <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                ) : undefined}
            />

            <div className="space-y-5">
                <section className="recipe-hero rounded-xl p-5 sm:p-6" data-accent={accent}>
                    <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                        <div className="min-w-0 flex-1">
                            <div className="flex items-start gap-3.5">
                                <span className="recipe-icon-tile h-12 w-12 shrink-0 rounded-xl" data-accent={accent}>
                                    {renderRecipeIcon(recipe, { className: 'h-6 w-6', strokeWidth: 1.7 })}
                                </span>
                                <div className="min-w-0">
                                    <p className="type-eyebrow-mono text-[var(--text-tertiary)]">
                                        {isPrompt ? 'Prompt recipe' : 'Kit recipe'} · {RECIPE_BUILDS_LABEL[recipe.builds]}
                                    </p>
                                    <h1 className="mt-1 text-2xl font-semibold tracking-normal text-[var(--text-primary)]">{recipe.name}</h1>
                                </div>
                            </div>
                            <p className="mt-3.5 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">{recipe.blurb}</p>
                            <div className="mt-4 flex flex-wrap gap-2">
                                {categoryLabel ? <MetaChip>{categoryLabel}</MetaChip> : null}
                                <MetaChip>{RECIPE_BUILDS_LABEL[recipe.builds]}</MetaChip>
                                {recipe.featured ? <MetaChip>Fast to value</MetaChip> : null}
                            </div>
                        </div>

                        <div className="flex shrink-0 flex-col items-start gap-2 lg:items-end">
                            <Button onClick={() => launchRecipe(recipe)} disabled={!canBuild} size="lg">
                                {isPrompt ? <Sparkles className="mr-2 h-4 w-4" /> : <PlayCircle className="mr-2 h-4 w-4" />}
                                Add to {podName}
                            </Button>
                            <p className="max-w-[15rem] text-xs leading-5 text-[var(--text-tertiary)] lg:text-right">
                                {canBuild ? helper : 'You don’t have permission to build in this pod.'}
                            </p>
                        </div>
                    </div>
                </section>

                <section className="surface-panel p-5">
                    <p className="type-eyebrow-mono text-[var(--text-tertiary)]">What you’ll get</p>
                    <h2 className="mt-1 text-lg font-semibold text-[var(--text-primary)]">
                        {isPrompt ? 'A working first version, built with you' : 'A full setup, installed with you'}
                    </h2>
                    <ul className="mt-4 grid gap-2.5 sm:grid-cols-3">
                        {highlights.map((point) => (
                            <li
                                key={point}
                                className="flex gap-2.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3.5 text-sm leading-6 text-[var(--text-secondary)]"
                            >
                                <Check className="mt-0.5 h-4 w-4 shrink-0 text-[var(--text-tertiary)]" strokeWidth={2} />
                                <span>{point}</span>
                            </li>
                        ))}
                    </ul>
                    <p className="mt-4 text-xs leading-5 text-[var(--text-tertiary)]">
                        Nothing is locked — open, use, and keep editing what gets built, and export it anytime.
                    </p>
                </section>

                {isPrompt && recipe.source.kind === 'prompt' ? (
                    <details className="surface-panel group p-5">
                        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 [&::-webkit-details-marker]:hidden">
                            <span>
                                <span className="type-eyebrow-mono text-[var(--text-tertiary)]">For the curious</span>
                                <span className="mt-1 block text-sm font-medium text-[var(--text-primary)]">Preview the exact prompt sent to the assistant</span>
                            </span>
                            <ChevronDown className="h-4 w-4 shrink-0 text-[var(--text-tertiary)] transition-transform group-open:rotate-180" />
                        </summary>
                        <pre className="mt-4 overflow-x-auto whitespace-pre-wrap rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] p-4 font-mono text-xs leading-6 text-[var(--text-secondary)]">
                            {recipe.source.prompt}
                        </pre>
                    </details>
                ) : kit ? (
                    <section className="surface-panel p-5">
                        <RecipeReadme kit={kit} />
                    </section>
                ) : null}
            </div>
        </ResourceIndexShell>
    );
}

function MetaChip({ children }: { children: ReactNode }) {
    return (
        <span className="inline-flex items-center rounded-full border border-[color:color-mix(in_srgb,var(--border-subtle)_80%,transparent)] bg-[var(--surface-1)] px-2.5 py-1 text-xs text-[var(--text-secondary)]">
            {children}
        </span>
    );
}
