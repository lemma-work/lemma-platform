import { describe, expect, it } from 'vitest';

import {
    REACH_RULE,
    STARTER_THEMES,
    getRecipeById,
    getRecipeLaunch,
    recipeCatalog,
    recipesForTheme,
} from '@/lib/recipes/recipes';

// The catalog is hand-maintained and cross-referenced by id in three directions
// (themes → recipes, prompt examples → recipes, onboarding → recipes). Nothing
// in the type system catches an id that no longer exists, and the symptom is a
// starter tile that silently disappears, so assert the references resolve.
describe('recipe catalog references', () => {
    it('gives every recipe a unique id', () => {
        const ids = recipeCatalog.map((recipe) => recipe.id);
        expect(new Set(ids).size).toBe(ids.length);
    });

    it.each(STARTER_THEMES.map((theme) => [theme.id, theme] as const))(
        'resolves every recipe id in the %s theme',
        (_id, theme) => {
            expect(recipesForTheme(theme)).toHaveLength(theme.recipeIds.length);
        },
    );

    it.each(STARTER_THEMES.map((theme) => [theme.id, theme] as const))(
        'points every prompt example in the %s theme at a real recipe',
        (_id, theme) => {
            for (const example of theme.promptExamples) {
                expect(getRecipeById(example.recipeId), example.title).not.toBeNull();
            }
        },
    );
});

// Only pod members can reach a surface or an app. A starter that quietly drops
// the rule is how the assistant ends up building a bot for customers who will
// only ever get a signup link back.
describe('the membership boundary', () => {
    it.each(recipeCatalog.filter((recipe) => recipe.source.kind === 'prompt').map((recipe) => [recipe.id, recipe] as const))(
        'carries the reach rule into the hidden instructions for %s',
        (_id, recipe) => {
            expect(getRecipeLaunch(recipe).instructions).toContain(REACH_RULE);
        },
    );

    it.each(STARTER_THEMES.flatMap((theme) => theme.promptExamples.map((example) => [example.title, example] as const)))(
        'carries the reach rule into the starter prompt "%s"',
        (_title, example) => {
            expect(example.prompt).toContain(REACH_RULE);
        },
    );

    it('keeps every surface-building recipe aimed at the pod, not at outsiders', () => {
        const surfaceRecipes = recipeCatalog.filter((recipe) => recipe.builds === 'surface');
        expect(surfaceRecipes.length).toBeGreaterThan(0);

        // Naming an outsider as the audience of a bot is the specific mistake:
        // they cannot message it. Serving them belongs to a connector-fed desk.
        const outsider = /\bcustomers?\b|\bleads?\b|\bclients?\b|\bapplicants?\b|\bcommunity\b/i;
        for (const recipe of surfaceRecipes) {
            const audience = [recipe.blurb, ...(recipe.examples ?? []), ...(recipe.highlights ?? [])].join(' ');
            expect(audience, `${recipe.id} pitches a surface at people who cannot reach it`).not.toMatch(outsider);
        }
    });
});
