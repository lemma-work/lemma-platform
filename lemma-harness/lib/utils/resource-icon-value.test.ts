import { describe, expect, it } from 'vitest';

import {
    formatIdentityIcon,
    identityHueClass,
    identityVariantSeed,
    isResourceIconGlyph,
    parseResourceIcon,
} from './resource-icon-value';

describe('parseResourceIcon', () => {
    it('reads nothing out of an absent icon', () => {
        expect(parseResourceIcon(null)).toBeNull();
        expect(parseResourceIcon(undefined)).toBeNull();
        expect(parseResourceIcon('')).toBeNull();
        expect(parseResourceIcon('   ')).toBeNull();
    });

    it('reads a single emoji as a glyph', () => {
        expect(parseResourceIcon('🚀')).toEqual({ kind: 'glyph', glyph: '🚀' });
    });

    it('trims before deciding, and stores what it trimmed', () => {
        expect(parseResourceIcon('  🚀  ')).toEqual({ kind: 'glyph', glyph: '🚀' });
    });

    it('handles the emoji that are not one code point', () => {
        // A flag is two regional indicators and contains no pictographic
        // character at all, which is why the "meaningful" test accepts them.
        expect(parseResourceIcon('🇮🇳')?.kind).toBe('glyph');
        // Zero-width joiner sequence.
        expect(parseResourceIcon('👨‍👩‍👧‍👦')?.kind).toBe('glyph');
        // Variation selector-16.
        expect(parseResourceIcon('❤️')?.kind).toBe('glyph');
        // Skin tone modifier.
        expect(parseResourceIcon('👋🏽')?.kind).toBe('glyph');
    });

    it('does not mistake digits for a glyph', () => {
        // Plain digits and '#' carry Emoji_Component, so without the second
        // test a pod named by a year would render as its own icon.
        expect(parseResourceIcon('2024')).toEqual({ kind: 'url', url: '2024' });
        expect(parseResourceIcon('#')).toEqual({ kind: 'url', url: '#' });
    });

    it('treats every shape of URL this field has ever held as a URL', () => {
        for (const url of [
            'https://cdn.example.com/pods/a.png',
            '/api/v1/icons/abcdef.png',
            'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
            'blob:http://localhost:3710/9f1e',
        ]) {
            expect(parseResourceIcon(url)).toEqual({ kind: 'url', url });
        }
    });

    it('refuses a wall of emoji rather than rendering one', () => {
        expect(parseResourceIcon('🚀'.repeat(20))?.kind).toBe('url');
    });
});

describe('isResourceIconGlyph', () => {
    it('answers only for glyphs', () => {
        expect(isResourceIconGlyph('🚀')).toBe(true);
        expect(isResourceIconGlyph('https://example.com/a.png')).toBe(false);
        expect(isResourceIconGlyph('')).toBe(false);
        expect(isResourceIconGlyph(null)).toBe(false);
    });
});

describe('generated identity variants', () => {
    it('round-trips a chosen variant', () => {
        expect(parseResourceIcon(formatIdentityIcon(7))).toEqual({ kind: 'identity', variant: 7 });
    });

    it('treats an absent value as the resource default, not a variant', () => {
        expect(parseResourceIcon(null)).toBeNull();
        expect(parseResourceIcon('')).toBeNull();
    });

    it('shifts the seed only for non-zero variants', () => {
        expect(identityVariantSeed('agent-1', 0)).toBe('agent-1');
        expect(identityVariantSeed('agent-1', 3)).toBe('agent-1#3');
        expect(identityVariantSeed('agent-1', 3)).not.toBe(identityVariantSeed('agent-1', 4));
    });

    it('falls back to the default rather than rendering a broken image', () => {
        // A malformed sentinel must never reach the <img> below it — that was
        // the exact failure mode the glyph branch was written to prevent.
        for (const bad of ['lemma-identity:', 'lemma-identity:abc', 'lemma-identity:-1', 'lemma-identity:1000', 'lemma-identity:1.5']) {
            expect(parseResourceIcon(bad)).toBeNull();
        }
    });

    it('does not mistake a real url or emoji for a variant', () => {
        expect(parseResourceIcon('https://cdn.example.com/a.png')?.kind).toBe('url');
        expect(parseResourceIcon('🦊')?.kind).toBe('glyph');
    });
});

describe('identityHueClass', () => {
    it('names one of the five sanctioned hue classes', () => {
        expect(identityHueClass(null, 'agent-1')).toMatch(/^lm-identity-hue-[0-4]$/);
    });

    it('is stable for the same seed and differs across seeds only as buckets allow', () => {
        expect(identityHueClass(null, 'agent-1')).toBe(identityHueClass(null, 'agent-1'));
        // Not a guarantee of difference, just that the class is derived from
        // the seed at all: enough seeds must produce more than one hue.
        const hues = new Set(
            Array.from({ length: 20 }, (_, i) => identityHueClass(null, `agent-${i}`)),
        );
        expect(hues.size).toBeGreaterThan(1);
    });

    it('follows a chosen variant, so the row and the face draw the same hue', () => {
        // Find a variant that changes the hue, then require the class to track
        // it — a row that ignored the variant would trim itself in the colour
        // of a face the resource no longer wears.
        const base = identityHueClass(null, 'agent-1');
        const shifted = [1, 2, 3, 4, 5, 6, 7, 8, 9]
            .map((variant) => identityHueClass(formatIdentityIcon(variant), 'agent-1'))
            .find((hue) => hue !== base);
        expect(shifted).toBeDefined();
        expect(identityHueClass(formatIdentityIcon(3), 'agent-1')).toBe(
            identityHueClass(null, 'agent-1#3'),
        );
    });

    it('keeps the resource hue under a picture or glyph icon', () => {
        // The identity is not drawn then, but the trim hue must stay the
        // resource's own — derived from the seed, not from the icon's shape.
        expect(identityHueClass('https://cdn.example.com/a.png', 'agent-1')).toBe(
            identityHueClass(null, 'agent-1'),
        );
        expect(identityHueClass('🦊', 'agent-1')).toBe(identityHueClass(null, 'agent-1'));
    });
});
