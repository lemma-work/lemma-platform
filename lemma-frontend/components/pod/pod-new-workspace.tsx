'use client';

import type { ComponentType } from 'react';
import Link from 'next/link';
import {
    ArrowRight,
    Bot,
    PanelsTopLeft,
    Plug,
    Sparkles,
    Workflow,
} from '@/components/ui/icons';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { renderRecipeIcon } from '@/components/recipes/recipe-icon';
import { Button } from '@/components/ui/button';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import {
    featuredRecipes,
    getRecipeAccent,
    type Recipe,
} from '@/lib/recipes/recipes';
import { useLaunchRecipe } from '@/lib/recipes/use-launch-recipe';

interface WorkspaceAction {
    title: string;
    prompt?: string;
    icon: ComponentType<{ className?: string; strokeWidth?: number }>;
}

const BUILD_ACTIONS: WorkspaceAction[] = [
    {
        title: 'Build an app',
        prompt: 'Build an app that ',
        icon: PanelsTopLeft,
    },
    {
        title: 'Automate work',
        prompt: 'Create a workflow that ',
        icon: Workflow,
    },
    {
        title: 'Create an agent',
        prompt: 'Create an agent that ',
        icon: Bot,
    },
];

function WorkspaceActionButton({
    action,
    disabled,
    onPreparePrompt,
}: {
    action: WorkspaceAction;
    disabled: boolean;
    onPreparePrompt: (prompt: string) => void;
}) {
    const Icon = action.icon;
    const content = (
        <>
            <Icon className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" strokeWidth={1.8} />
            <span className="truncate text-sm text-[var(--text-secondary)]">{action.title}</span>
        </>
    );

    return (
        <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => action.prompt && onPreparePrompt(action.prompt)}
            disabled={disabled}
            className="h-8 min-h-8 w-auto justify-start gap-2 rounded-md px-2 font-normal hover:text-[var(--text-primary)]"
        >
            {content}
        </Button>
    );
}

function formatPodName(value: string | null | undefined) {
    const cleaned = (value || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!cleaned) return null;
    return cleaned
        .split(' ')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

function recipePairForPod(recipes: Recipe[], podId: string) {
    if (recipes.length <= 2) return recipes;

    const day = new Date().toISOString().slice(0, 10);
    let seed = 0;
    for (const character of `${podId}:${day}`) {
        seed = ((seed << 5) - seed + character.charCodeAt(0)) | 0;
    }

    const firstIndex = Math.abs(seed) % recipes.length;
    const offset = 1 + (Math.abs(seed >> 3) % (recipes.length - 1));
    const secondIndex = (firstIndex + offset) % recipes.length;
    return [recipes[firstIndex], recipes[secondIndex]];
}

function RecipeLaunchRow({
    recipe,
    disabled,
    onLaunch,
}: {
    recipe: Recipe;
    disabled: boolean;
    onLaunch: () => void;
}) {
    const accent = getRecipeAccent(recipe);

    return (
        <Button
            type="button"
            variant="ghost"
            onClick={onLaunch}
            disabled={disabled}
            className="h-8 min-h-8 w-auto justify-start gap-2 rounded-md px-2 text-left disabled:opacity-50"
        >
            <span className="recipe-icon-tile h-5 w-5 shrink-0 rounded" data-accent={accent}>
                {renderRecipeIcon(recipe, { className: 'h-3 w-3', strokeWidth: 1.8 })}
            </span>
            <span className="truncate text-sm text-[var(--text-secondary)]">
                {recipe.name}
            </span>
        </Button>
    );
}

export function PodNewWorkspace({
    podId,
    onPreparePrompt,
}: {
    podId: string;
    onPreparePrompt: (prompt: string) => void;
}) {
    const assistant = useAIAssistant();
    const podAccess = usePodAccess(podId);
    const { data: pod } = usePod(podId);
    const podName = formatPodName(pod?.name);
    const { launchRecipe } = useLaunchRecipe(podId, { podName });
    const canWriteConversations = podAccess.can('conversation.write');
    const isBusy = assistant.isLoading || assistant.isOpenedConversationRunning;
    const recipes = recipePairForPod(featuredRecipes, podId);

    return (
        <div className="w-full text-[var(--text-primary)]">
            <section className="mx-auto w-full max-w-5xl px-6 pt-8 sm:pt-10">
                <div className="flex max-w-3xl items-start gap-2.5 px-2">
                    <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-[var(--text-tertiary)]" strokeWidth={1.8} />
                    <p className="text-sm leading-5 text-[var(--text-secondary)]">
                        <span className="font-medium text-[var(--text-primary)]">Lemma Assist</span> is this pod’s core assistant. Give it work to complete now, or build apps, agents, and workflows that keep doing it.
                    </p>
                </div>

                <div className="mt-6 space-y-1">
                    <div className="flex flex-wrap items-center gap-x-1 gap-y-1">
                        <div className="w-24 shrink-0 px-2 text-xs font-medium uppercase tracking-wide text-[var(--text-tertiary)]">
                            Build
                        </div>
                        {BUILD_ACTIONS.map((action) => (
                            <WorkspaceActionButton
                                key={action.title}
                                action={action}
                                disabled={!canWriteConversations || isBusy}
                                onPreparePrompt={onPreparePrompt}
                            />
                        ))}
                        <Button asChild variant="ghost" className="h-8 min-h-8 w-auto justify-start gap-2 rounded-md px-2 font-normal">
                            <Link href={`/pod/${podId}/connectors`}>
                                <Plug className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" strokeWidth={1.8} />
                                <span className="truncate text-sm text-[var(--text-secondary)]">Connect a tool</span>
                            </Link>
                        </Button>
                    </div>

                    {recipes.length > 0 ? (
                        <div className="flex flex-wrap items-center gap-x-1 gap-y-1">
                            <div className="w-24 shrink-0 px-2 text-xs text-[var(--text-tertiary)]">
                                From a recipe
                            </div>
                            {recipes.map((recipe) => (
                                <RecipeLaunchRow
                                    key={recipe.id}
                                    recipe={recipe}
                                    disabled={!canWriteConversations || isBusy}
                                    onLaunch={() => launchRecipe(recipe)}
                                />
                            ))}
                            <Link
                                href={`/pod/${podId}/recipes`}
                                className="custom-focus-ring inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                            >
                                All recipes
                                <ArrowRight className="h-3.5 w-3.5" />
                            </Link>
                        </div>
                    ) : null}
                </div>
            </section>
        </div>
    );
}
