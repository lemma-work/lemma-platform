'use client';

import { use, useMemo, useState } from 'react';
import { PackageOpen, Search } from '@/components/ui/icons';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { EmptyState } from '@/components/shared/empty-state';
import { ResourceIndexHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { RecipeCard, RecipeFeatureCard } from '@/components/recipes/recipe-card';
import { Input } from '@/components/ui/input';
import { usePod } from '@/lib/hooks/use-pods';
import {
    RECIPE_CATEGORIES,
    featuredRecipes,
    recipeCatalog,
    recipesByCategory,
} from '@/lib/recipes/recipes';
import { useLaunchRecipe } from '@/lib/recipes/use-launch-recipe';

function formatPodName(value: string | null | undefined) {
    const cleaned = (value || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!cleaned) return null;
    return cleaned.split(' ').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

export default function PodRecipesPage({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const { data: pod } = usePod(podId);
    const podName = formatPodName(pod?.name);
    const { launchRecipe } = useLaunchRecipe(podId, { podName });
    const [query, setQuery] = useState('');

    const normalized = query.trim().toLowerCase();
    const searching = normalized.length > 0;

    const matches = useMemo(() => {
        if (!normalized) return [];
        return recipeCatalog.filter((recipe) => {
            const haystack = [recipe.name, recipe.blurb, recipe.builds, recipe.category].join(' ').toLowerCase();
            return haystack.includes(normalized);
        });
    }, [normalized]);

    const featured = featuredRecipes.slice(0, 3);

    return (
        <ProtectedRoute>
            <ResourceIndexShell>
                <ResourceIndexHeader
                    title="Recipes"
                    productIconKind="apps"
                    actions={(
                        <div className="relative w-full sm:w-72">
                            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
                            <Input
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder="Search recipes..."
                                className="pl-9"
                            />
                        </div>
                    )}
                />

                <p className="mb-5 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">
                    Recipes add capability to this pod — a prompt the assistant builds into a working app, a bot people
                    message, or a full kit. Pick one and the assistant builds it with you in a conversation.
                </p>

                {searching ? (
                    matches.length > 0 ? (
                        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                            {matches.map((recipe) => (
                                <RecipeCard key={recipe.id} podId={podId} recipe={recipe} onLaunch={() => launchRecipe(recipe)} />
                            ))}
                        </section>
                    ) : (
                        <EmptyState
                            variant="compact"
                            icon={<PackageOpen className="h-4 w-4" />}
                            title="No recipes match this search"
                            description="Try a different outcome, workflow, or domain."
                        />
                    )
                ) : (
                    <div className="space-y-9">
                        {featured.length > 0 ? (
                            <section>
                                <h2 className="text-base font-medium text-[var(--text-primary)]">Fastest to value</h2>
                                <p className="mt-0.5 text-sm leading-6 text-[var(--text-tertiary)]">Set up in a couple of minutes, useful immediately.</p>
                                <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                                    {featured.map((recipe) => (
                                        <RecipeFeatureCard key={recipe.id} podId={podId} recipe={recipe} onLaunch={() => launchRecipe(recipe)} />
                                    ))}
                                </div>
                            </section>
                        ) : null}

                        {RECIPE_CATEGORIES.map((category) => {
                            const items = recipesByCategory(category.id);
                            if (items.length === 0) return null;

                            return (
                                <section key={category.id}>
                                    <h2 className="text-base font-medium text-[var(--text-primary)]">{category.label}</h2>
                                    <p className="mt-0.5 text-sm leading-6 text-[var(--text-tertiary)]">{category.blurb}</p>
                                    <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                                        {items.map((recipe) => (
                                            <RecipeCard key={recipe.id} podId={podId} recipe={recipe} onLaunch={() => launchRecipe(recipe)} />
                                        ))}
                                    </div>
                                </section>
                            );
                        })}
                    </div>
                )}
            </ResourceIndexShell>
        </ProtectedRoute>
    );
}
