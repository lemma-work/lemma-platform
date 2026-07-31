'use client';

import { useMemo, useState } from 'react';
import Image from 'next/image';
import { ArrowUpRight, Sparkles } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import {
    recipesForTheme,
    type Recipe,
    type StarterTheme,
    type StarterThemeId,
} from '@/lib/recipes/recipes';
import { cn } from '@/lib/utils';
import { renderRecipeIcon } from './recipe-icon';

const THEME_LOGOS: Partial<Record<StarterThemeId, { src: string; alt: string }>> = {
    whatsapp: { src: '/surfaces/whatsapp.png', alt: 'WhatsApp' },
    telegram: { src: '/surfaces/telegram.png', alt: 'Telegram' },
    slack: { src: '/surfaces/slack.png', alt: 'Slack' },
    email: { src: '/surfaces/gmail.png', alt: 'Email' },
    teams: { src: '/surfaces/teams.png', alt: 'Microsoft Teams' },
};

export function StarterThemePicker({
    themes,
    onLaunch,
}: {
    themes: StarterTheme[];
    onLaunch: (recipe: Recipe, message: string) => void;
}) {
    const [activeThemeId, setActiveThemeId] = useState<StarterThemeId | undefined>(themes[0]?.id);
    const activeTheme = themes.find((theme) => theme.id === activeThemeId) ?? themes[0];
    const recipes = useMemo(() => activeTheme ? recipesForTheme(activeTheme) : [], [activeTheme]);
    const recipeById = new Map(recipes.map((recipe) => [recipe.id, recipe]));
    const prompts = activeTheme?.promptExamples.flatMap((example) => {
        const recipe = recipeById.get(example.recipeId);
        return recipe ? [{ ...example, recipe }] : [];
    }) ?? [];

    if (!activeTheme) return null;

    return (
        <div className="starter-theme-picker">
            <div className="starter-theme-rail" role="tablist" aria-label="Starter categories">
                {themes.map((theme) => {
                    const themeRecipes = recipesForTheme(theme);
                    const leadRecipe = themeRecipes[0];
                    const logo = THEME_LOGOS[theme.id];
                    const active = theme.id === activeTheme.id;

                    return (
                        <Button
                            key={theme.id}
                            type="button"
                            variant="ghost"
                            role="tab"
                            aria-selected={active}
                            aria-controls="starter-theme-prompts"
                            onMouseEnter={() => setActiveThemeId(theme.id)}
                            onFocus={() => setActiveThemeId(theme.id)}
                            onClick={() => setActiveThemeId(theme.id)}
                            className={cn('starter-theme-tab', active && 'starter-theme-tab-active')}
                            data-theme={theme.id}
                        >
                            <span className={cn('starter-theme-tab-icon', logo && 'starter-theme-tab-icon-logo')}>
                                {logo ? (
                                    <Image src={logo.src} alt={logo.alt} width={20} height={20} className="object-contain" />
                                ) : leadRecipe ? (
                                    renderRecipeIcon(leadRecipe, { className: 'h-4 w-4', strokeWidth: 1.8 })
                                ) : (
                                    <Sparkles className="h-4 w-4" />
                                )}
                            </span>
                            <span>{theme.name}</span>
                        </Button>
                    );
                })}
            </div>

            <div
                id="starter-theme-prompts"
                className="starter-theme-inline-prompts"
                role="tabpanel"
                aria-label={`${activeTheme.name} example prompts`}
                data-theme={activeTheme.id}
            >
                <div>
                    {prompts.map(({ recipe, title, prompt }, index) => (
                        <Button
                            key={`${recipe.id}-${index}`}
                            type="button"
                            variant="ghost"
                            title={title}
                            onClick={() => onLaunch(recipe, prompt)}
                            className="starter-theme-inline-prompt"
                        >
                            <Sparkles className="h-3.5 w-3.5 shrink-0" />
                            <span>{title}</span>
                            <span className="starter-theme-inline-prompt-action">
                                Use prompt
                                <ArrowUpRight className="h-4 w-4" />
                            </span>
                        </Button>
                    ))}
                </div>
            </div>
        </div>
    );
}
