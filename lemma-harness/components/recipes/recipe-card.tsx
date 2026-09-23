'use client';

import { PlayCircle, Sparkles } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { RECIPE_OUTPUT_LABEL, getRecipeAccent, type Recipe } from '@/lib/recipes/recipes';
import { renderRecipeIcon } from './recipe-icon';

function actionLabel(recipe: Recipe) {
    return recipe.source.kind === 'prompt' ? 'Start' : 'Install';
}

function ActionIcon({ recipe }: { recipe: Recipe }) {
    return recipe.source.kind === 'prompt'
        ? <Sparkles className="h-3.5 w-3.5" />
        : <PlayCircle className="h-3.5 w-3.5" />;
}

export function RecipeCard({ recipe, onLaunch }: { recipe: Recipe; onLaunch: () => void }) {
    const accent = getRecipeAccent(recipe);

    return (
        <article className="resource-index-card group flex min-h-52 flex-col overflow-hidden p-0">
            <Button
                type="button"
                variant="quiet"
                onClick={onLaunch}
                className="h-auto flex-1 items-stretch justify-start whitespace-normal rounded-none p-4 text-left"
            >
                <RecipeCardBody recipe={recipe} accent={accent} />
            </Button>
            <div className="flex items-center gap-2 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_60%,transparent)] px-4 py-3">
                <Button variant="primary" size="sm" onClick={onLaunch} className="h-8 gap-1.5 rounded-md px-3 text-xs">
                    <ActionIcon recipe={recipe} />
                    {actionLabel(recipe)}
                </Button>
            </div>
        </article>
    );
}

function RecipeCardBody({ recipe, accent }: { recipe: Recipe; accent: ReturnType<typeof getRecipeAccent> }) {
    return (
        <div>
            <div className="flex items-start gap-3">
                <span className="recipe-icon-tile h-9 w-9 shrink-0 rounded-lg" data-accent={accent}>
                    {renderRecipeIcon(recipe, { className: 'h-[18px] w-[18px]', strokeWidth: 1.8 })}
                </span>
                <div className="min-w-0 flex-1">
                    <h3 className="truncate text-base font-medium text-[var(--text-primary)]">{recipe.name}</h3>
                    <p className="mt-0.5 line-clamp-1 text-xs text-[var(--text-tertiary)]">{recipe.kicker}</p>
                </div>
            </div>
            <p className="mt-3 line-clamp-2 min-h-10 text-sm leading-6 text-[var(--text-secondary)]">{recipe.blurb}</p>
            {recipe.examples?.length ? (
                <p className="mt-3 line-clamp-2 text-xs leading-5 text-[var(--text-tertiary)]">
                    Good for {recipe.examples.join(' · ')}
                </p>
            ) : null}
            <RecipeOutputs recipe={recipe} />
        </div>
    );
}

function RecipeOutputs({ recipe }: { recipe: Recipe }) {
    return (
        <div className="mt-3 flex flex-wrap gap-1.5">
            {recipe.outputs.slice(0, 4).map((output) => (
                <span key={output} className="chip chip-sm chip-muted">
                    {RECIPE_OUTPUT_LABEL[output]}
                </span>
            ))}
        </div>
    );
}
