import { describe, expect, it } from 'vitest';

import {
    defaultVisibilityFor,
    normalizeResourceVisibility,
    reachesOutsidePod,
    VISIBILITY_VALUES,
} from './resource-visibility';

describe('normalizeResourceVisibility', () => {
    it('passes through every canonical level', () => {
        for (const level of VISIBILITY_VALUES) {
            expect(normalizeResourceVisibility(level)).toBe(level);
        }
    });

    it('maps legacy spellings onto their level', () => {
        expect(normalizeResourceVisibility('PRIVATE')).toBe('PERSONAL');
        expect(normalizeResourceVisibility('OWNER')).toBe('PERSONAL');
        expect(normalizeResourceVisibility('ALL')).toBe('POD');
    });

    it('is case- and whitespace-insensitive', () => {
        expect(normalizeResourceVisibility('  restricted ')).toBe('RESTRICTED');
    });

    it('falls back to POD, the narrower reading, for empty or unknown values', () => {
        expect(normalizeResourceVisibility(null)).toBe('POD');
        expect(normalizeResourceVisibility(undefined)).toBe('POD');
        expect(normalizeResourceVisibility('')).toBe('POD');
        expect(normalizeResourceVisibility('nonsense')).toBe('POD');
    });
});

describe('VISIBILITY_VALUES', () => {
    it('runs narrow to wide', () => {
        expect(VISIBILITY_VALUES).toEqual(['PERSONAL', 'POD', 'RESTRICTED', 'PUBLIC']);
    });
});

describe('defaultVisibilityFor', () => {
    it('is PUBLIC for apps and POD for everything else', () => {
        // Mirrors the backend: apps are created PUBLIC because they are served
        // on their own public host, every other resource starts POD. A badge
        // that hides the wrong "default" reads as the opposite of the truth.
        expect(defaultVisibilityFor('app')).toBe('PUBLIC');
        expect(defaultVisibilityFor('agent')).toBe('POD');
        expect(defaultVisibilityFor('datastore_table')).toBe('POD');
        expect(defaultVisibilityFor(undefined)).toBe('POD');
        expect(defaultVisibilityFor(null)).toBe('POD');
    });
});

describe('reachesOutsidePod', () => {
    it('is true only for the level a non-member can open', () => {
        // This decides whether a share link gets the /s/ wrapper. Getting it
        // wrong hands out a /pod/ URL that walls the recipient off, which is the
        // failure the wrapper exists to fix.
        expect(reachesOutsidePod('PUBLIC')).toBe(true);
        expect(reachesOutsidePod('POD')).toBe(false);
        expect(reachesOutsidePod('RESTRICTED')).toBe(false);
        expect(reachesOutsidePod('PERSONAL')).toBe(false);
    });
});
