import { describe, expect, it } from 'vitest';

import { planRename, renameStepMs } from '@/lib/hooks/use-typed-rename';

describe('planRename', () => {
    it('types a server-generated title in over the local stand-in', () => {
        const plan = planRename('who has the most centuries', "Ashwin's records", false);

        expect(plan.kind).toBe('type');
        expect(plan.kind === 'type' && plan.characters.join('')).toBe("Ashwin's records");
    });

    it('does not animate a row seeing its title for the first time', () => {
        // Otherwise every row in a freshly loaded sidebar types itself in at once.
        expect(planRename('', 'Quarterly numbers', false)).toEqual({
            kind: 'settle',
            text: 'Quarterly numbers',
        });
    });

    it('does not animate a title going away', () => {
        expect(planRename('Quarterly numbers', '', false)).toEqual({ kind: 'settle', text: '' });
    });

    it('respects a reduced-motion preference', () => {
        expect(planRename('draft', 'Quarterly numbers', true)).toEqual({
            kind: 'settle',
            text: 'Quarterly numbers',
        });
    });

    it('steps by code point, so a title in any script survives the reveal', () => {
        // Sliced by code unit, the halfway frame of this one is a replacement box.
        const plan = planRename('trip', '日本の春 🌸', false);

        expect(plan.kind === 'type' && plan.characters).toEqual(['日', '本', 'の', '春', ' ', '🌸']);
    });
});

describe('renameStepMs', () => {
    it('slows a short title down and speeds a long one up', () => {
        expect(renameStepMs(4)).toBeGreaterThan(renameStepMs(80));
    });

    it('keeps even a maximum-length title short enough to sit through', () => {
        // The generated ones are 3-6 words; 120 is the cap on the fallback cut
        // from the user's own message, and the floor below is what stops it
        // ticking sub-frame rather than what sets its length.
        expect(renameStepMs(120) * 120).toBeLessThan(1500);
    });

    it('never ticks faster than the eye can follow', () => {
        expect(renameStepMs(10_000)).toBeGreaterThanOrEqual(12);
    });
});
