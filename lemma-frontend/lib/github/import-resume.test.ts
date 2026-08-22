import { describe, expect, it } from 'vitest';

import {
    buildImportReturnUrl,
    hasResumeMarker,
    withoutResumeMarker,
} from './import-resume';

const IMPORT_URL = 'https://lemma.work/import/github/wineforyourplate/gymmit';

describe('carrying an install intent across signup', () => {
    it('stamps the destination and the resume marker on the return URL', () => {
        const url = new URL(buildImportReturnUrl(IMPORT_URL, 'new'));

        expect(url.pathname).toBe('/import/github/wineforyourplate/gymmit');
        expect(url.searchParams.get('destination')).toBe('new');
        expect(url.searchParams.get('resume')).toBe('1');
    });

    it('keeps the destination the visitor actually chose', () => {
        const url = new URL(buildImportReturnUrl(IMPORT_URL, 'existing'));
        expect(url.searchParams.get('destination')).toBe('existing');
    });

    it('does not duplicate params when the URL already carries them', () => {
        const once = buildImportReturnUrl(IMPORT_URL, 'new');
        const twice = buildImportReturnUrl(once, 'new');

        expect(twice).toBe(once);
        expect([...new URL(twice).searchParams.keys()]).toEqual(['destination', 'resume']);
    });

    it('recognises a return leg, and only a return leg', () => {
        expect(hasResumeMarker(buildImportReturnUrl(IMPORT_URL, 'new'))).toBe(true);
        expect(hasResumeMarker(IMPORT_URL)).toBe(false);
        // Arriving with a destination but no marker is a shared link, not a
        // return from signup, and must not install anything by itself.
        expect(hasResumeMarker(`${IMPORT_URL}?destination=new`)).toBe(false);
    });

    it('clears the marker while leaving the destination alone', () => {
        const cleared = new URL(withoutResumeMarker(buildImportReturnUrl(IMPORT_URL, 'new')));

        expect(cleared.searchParams.has('resume')).toBe(false);
        expect(cleared.searchParams.get('destination')).toBe('new');
        expect(hasResumeMarker(cleared.toString())).toBe(false);
    });
});
