'use client';

import { use, useMemo, useState } from 'react';
import { PackageOpen, Search } from '@/components/ui/icons';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { EmptyState } from '@/components/shared/empty-state';
import { ResourceIndexHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { RecipeCard } from '@/components/recipes/recipe-card';
import { StarterThemePicker } from '@/components/recipes/starter-theme-card';
import { Input } from '@/components/ui/input';
import { usePod } from '@/lib/hooks/use-pods';
import {
    STARTER_THEMES,
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
            const haystack = [
                recipe.name,
                recipe.kicker,
                recipe.blurb,
                recipe.builds,
                recipe.category,
                ...recipe.outputs,
                ...(recipe.platforms || []),
                ...(recipe.examples || []),
            ].join(' ').toLowerCase();
            return haystack.includes(normalized);
        });
    }, [normalized]);

    const publishedKits = recipesByCategory('published');

    return (
        <ProtectedRoute>
            <ResourceIndexShell>
                <ResourceIndexHeader
                    title="Add capability"
                    productIconKind="apps"
                    actions={(
                        <div className="relative w-full sm:w-72">
                            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
                            <Input
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder="Search starters..."
                                className="pl-9"
                            />
                        </div>
                    )}
                />

                <p className="mb-5 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">
                    Choose a direction, then start from an example prompt. Published kits stay available below when you want a complete source-backed setup.
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
                            title="No starters match this search"
                            description="Try a product shape, channel, or operating loop."
                        />
                    )
                ) : (
                    <div className="space-y-9">
                        <section>
                            <h2 className="text-base font-medium text-[var(--text-primary)]">Choose a direction</h2>
                            <p className="mt-0.5 text-sm leading-6 text-[var(--text-tertiary)]">Hover or click a category to see prompts you can start immediately.</p>
                            <div className="mt-3">
                                <StarterThemePicker
                                    themes={STARTER_THEMES}
                                    onLaunch={(recipe, message) => launchRecipe(recipe, { message })}
                                />
                            </div>
                        </section>
                        {publishedKits.length > 0 ? (
                            <section>
                                <h2 className="text-base font-medium text-[var(--text-primary)]">Published kits</h2>
                                <p className="mt-0.5 text-sm leading-6 text-[var(--text-tertiary)]">Complete source-backed setups ready to install into this pod.</p>
                                <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                                    {publishedKits.map((recipe) => (
                                        <RecipeCard key={recipe.id} podId={podId} recipe={recipe} onLaunch={() => launchRecipe(recipe)} />
                                    ))}
                                </div>
                            </section>
                        ) : null}
                    </div>
                )}
            </ResourceIndexShell>
        </ProtectedRoute>
    );
}
