import { describe, expect, it } from 'vitest';

import {
    isSocialCardVariant,
    resolveSocialCardCopy,
    socialCardPath,
} from './social-card';

describe('social-card', () => {
    it('keeps the site promise concise', () => {
        expect(resolveSocialCardCopy({ variant: 'site' })).toEqual({
            eyebrow: 'THE RUNTIME FOR AGENT-BUILT SOFTWARE',
            title: "The software you need doesn't exist yet.",
            detail: 'Your coding agent can write it. Lemma makes it something your team can actually use.',
            label: 'lemma.work',
        });
    });

    it('supports the full card family', () => {
        expect(['site', 'run', 'build', 'made', 'join'].every(isSocialCardVariant)).toBe(true);
        expect(isSocialCardVariant('private')).toBe(false);
    });

    it('bounds untrusted card copy', () => {
        const card = resolveSocialCardCopy({
            variant: 'run',
            title: 'x'.repeat(100),
            label: 'y'.repeat(200),
        });
        expect(card.title.length).toBe(64);
        expect(card.label.length).toBe(120);
    });

    it('builds an encoded dynamic image path', () => {
        expect(
            socialCardPath({
                variant: 'run',
                title: 'Research & insights',
                label: 'github.com/acme/research',
            }),
        ).toBe(
            '/api/social-card?variant=run&title=Research+%26+insights&label=github.com%2Facme%2Fresearch',
        );
    });
});
