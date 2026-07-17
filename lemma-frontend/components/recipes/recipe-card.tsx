'use client';

import Link from 'next/link';
import { ArrowRight, PlayCircle, Sparkles } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { RECIPE_BUILDS_LABEL, getRecipeAccent, type Recipe } from '@/lib/recipes/recipes';
import { renderRecipeIcon } from './recipe-icon';

function kindLabel(recipe: Recipe) {
    return recipe.source.kind === 'prompt' ? 'Prompt' : 'Kit';
}

function actionLabel(recipe: Recipe) {
    return recipe.source.kind === 'prompt' ? 'Build' : 'Install';
}

function ActionIcon({ recipe }: { recipe: Recipe }) {
    return recipe.source.kind === 'prompt'
        ? <Sparkles className="h-3.5 w-3.5" />
        : <PlayCircle className="h-3.5 w-3.5" />;
}

// Style B — colored icon tile, clean card. The workhorse for grids.
export function RecipeCard({ podId, recipe, onLaunch }: { podId: string; recipe: Recipe; onLaunch: () => void }) {
    const detailHref = `/pod/${podId}/recipes/${encodeURIComponent(recipe.id)}`;
    const accent = getRecipeAccent(recipe);

    return (
        <article className="resource-index-card group overflow-hidden p-0">
            <Link href={detailHref} className="custom-focus-ring block p-4">
                <div className="flex items-start gap-3">
                    <span className="recipe-icon-tile h-9 w-9 shrink-0 rounded-lg" data-accent={accent}>
                        {renderRecipeIcon(recipe, { className: 'h-[18px] w-[18px]', strokeWidth: 1.8 })}
                    </span>
                    <div className="min-w-0 flex-1">
                        <p className="type-eyebrow-mono text-[var(--text-tertiary)]">
                            {kindLabel(recipe)} · {RECIPE_BUILDS_LABEL[recipe.builds]}
                        </p>
                        <h3 className="mt-1 truncate text-base font-medium text-[var(--text-primary)]">{recipe.name}</h3>
                    </div>
                </div>
                <p className="mt-3 line-clamp-2 min-h-10 text-sm leading-6 text-[var(--text-secondary)]">{recipe.blurb}</p>
            </Link>
            <div className="flex items-center gap-2 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_60%,transparent)] px-4 py-3">
                <Button size="sm" onClick={onLaunch} className="h-8 gap-1.5 rounded-md px-3 text-xs">
                    <ActionIcon recipe={recipe} />
                    {actionLabel(recipe)}
                </Button>
                <Link
                    href={detailHref}
                    className="custom-focus-ring ml-auto inline-flex h-8 items-center gap-1 rounded-md px-2.5 text-xs text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                >
                    Details
                    <ArrowRight className="h-3.5 w-3.5" />
                </Link>
            </div>
        </article>
    );
}

// Style C — preview-forward. A glimpse of what gets built, for featured rows.
export function RecipeFeatureCard({ podId, recipe, onLaunch }: { podId: string; recipe: Recipe; onLaunch: () => void }) {
    const detailHref = `/pod/${podId}/recipes/${encodeURIComponent(recipe.id)}`;
    const accent = getRecipeAccent(recipe);

    return (
        <article className="resource-index-card group overflow-hidden p-0">
            <Link href={detailHref} className="custom-focus-ring block">
                <div className="recipe-preview flex flex-col gap-2 px-4 py-3.5" data-accent={accent}>
                    <span className="recipe-preview-bar h-1.5 w-[46%] rounded-full" />
                    <span className="recipe-preview-line h-1.5 w-[88%] rounded-full" />
                    <span className="recipe-preview-line h-1.5 w-[72%] rounded-full" />
                </div>
                <div className="p-4">
                    <div className="flex items-center gap-2.5">
                        <span className="recipe-icon-tile h-8 w-8 shrink-0 rounded-lg" data-accent={accent}>
                            {renderRecipeIcon(recipe, { className: 'h-4 w-4', strokeWidth: 1.8 })}
                        </span>
                        <h3 className="min-w-0 truncate text-base font-medium text-[var(--text-primary)]">{recipe.name}</h3>
                    </div>
                    <p className="mt-2.5 line-clamp-2 min-h-10 text-sm leading-6 text-[var(--text-secondary)]">{recipe.blurb}</p>
                </div>
            </Link>
            <div className="flex items-center gap-2 px-4 pb-4">
                <Button size="sm" onClick={onLaunch} className="h-8 gap-1.5 rounded-md px-3 text-xs">
                    <ActionIcon recipe={recipe} />
                    {actionLabel(recipe)}
                </Button>
                <Link
                    href={detailHref}
                    className="custom-focus-ring ml-auto inline-flex h-8 items-center gap-1 rounded-md px-2.5 text-xs text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                >
                    Details
                    <ArrowRight className="h-3.5 w-3.5" />
                </Link>
            </div>
        </article>
    );
}
