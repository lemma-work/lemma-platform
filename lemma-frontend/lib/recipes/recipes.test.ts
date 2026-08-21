import { describe, expect, it } from 'vitest';

import {
    FIRST_RUN_DELIGHT,
    REACH_RULE,
    STARTER_THEMES,
    getRecipeById,
    getRecipeLaunch,
    recipeCatalog,
    recipesForTheme,
} from '@/lib/recipes/recipes';

const promptRecipes = recipeCatalog.filter((recipe) => recipe.source.kind === 'prompt');

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

// The regression this exists for: the hidden instructions opened with an intake
// form — who works on this, where the work happens, which inboxes are involved —
// standing between someone who had just picked a recipe and the thing they
// picked. Connecting is still the point; it just comes after something runs.
describe('a recipe builds before it asks', () => {
    it.each(promptRecipes.map((recipe) => [recipe.id, recipe] as const))(
        'puts the build ahead of the setup for %s',
        (_id, recipe) => {
            const { instructions } = getRecipeLaunch(recipe);
            const build = instructions.indexOf('Build the smallest version that actually works');
            const setup = instructions.indexOf('Setup comes after something works, not before it');

            expect(build).toBeGreaterThan(-1);
            expect(setup).toBeGreaterThan(build);
        },
    );

    it.each(promptRecipes.map((recipe) => [recipe.id, recipe] as const))(
        'asks one question at a time in %s, not a list',
        (_id, recipe) => {
            const { instructions } = getRecipeLaunch(recipe);

            expect(instructions).toContain('One short question per turn, never a list');
            expect(instructions).not.toContain('one or two friendly questions at a time');
        },
    );

    it('stops collecting names before anything exists', () => {
        for (const recipe of promptRecipes) {
            const { instructions } = getRecipeLaunch(recipe);
            expect(instructions).toContain('Do not open by collecting names');
            expect(instructions).not.toContain('so you can invite those people to the workspace');
        }
    });

    it('builds the agent before wiring the surface it is reached through', () => {
        const surfaceRecipe = promptRecipes.find((recipe) => recipe.builds === 'surface');
        expect(surfaceRecipe).toBeDefined();

        expect(getRecipeLaunch(surfaceRecipe!).instructions).toContain(
            'build the agent first — there has to be something to talk to',
        );
    });
});

// FIRST_RUN_DELIGHT is concatenated on top of the recipe instructions, so the
// two have to want the same thing. "Never block the wow on setup" used to read
// as permission to skip the asking entirely.
describe('the first-run framing', () => {
    it('reads offering as sequence, not as license to act unasked', () => {
        expect(FIRST_RUN_DELIGHT).toContain('Offering is not blocking; doing it unasked is not momentum');
        expect(FIRST_RUN_DELIGHT).not.toContain('Never block the wow on setup');
    });

    it('paces itself the same way the rest of onboarding does', () => {
        expect(FIRST_RUN_DELIGHT).toContain('One thing per turn');
        expect(FIRST_RUN_DELIGHT).toContain('never stack a result and the next question');
    });

    it('names a concept before using it, since they have seen none of them', () => {
        expect(FIRST_RUN_DELIGHT).toContain('name a thing and say what it is for the first time you use it');
    });

    it('puts the delight in the build rather than in unasked-for output', () => {
        expect(FIRST_RUN_DELIGHT).toContain('Put the care into the thing itself');
        expect(FIRST_RUN_DELIGHT).not.toContain('slip in one small delightful touch');
    });

    it('rides on top of a recipe launch when it is someone\'s first build', () => {
        const recipe = promptRecipes[0];
        const { instructions, metadata } = getRecipeLaunch(recipe, { firstRun: true });

        expect(instructions.startsWith(FIRST_RUN_DELIGHT)).toBe(true);
        expect(metadata).toMatchObject({ first_run: true });
        expect(getRecipeLaunch(recipe).instructions).not.toContain(FIRST_RUN_DELIGHT);
    });
});
