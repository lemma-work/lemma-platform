import { describe, expect, it } from 'vitest';

import {
    PUBLIC_TEMPLATES,
    templateCoverPath,
    templateRunHref,
} from './catalog';

describe('public template catalogue', () => {
    it('routes every template through the GitHub bundle installer', () => {
        expect(PUBLIC_TEMPLATES).toHaveLength(10);
        expect(PUBLIC_TEMPLATES.map((template) => templateRunHref(template))).toEqual([
            '/import/github/deepak-jha-kgp/roundtable',
            '/import/github/deepak-jha-kgp/panini',
            '/import/github/deepak-jha-kgp/frontdesk',
            '/import/github/deepak-jha-kgp/smart-inbox',
            '/import/github/deepak-jha-kgp/sidekick',
            '/import/github/deepak-jha-kgp/lemma-design',
            '/import/github/deepak-jha-kgp/nachiketa',
            '/import/github/deepak-jha-kgp/drop',
            '/import/github/deepak-jha-kgp/meal',
            '/import/github/deepak-jha-kgp/lemma-gtm',
        ]);
    });

    it('uses a local cover for every published template', () => {
        expect(PUBLIC_TEMPLATES.map((template) => templateCoverPath(template))).toEqual([
            '/templates/roundtable/social-preview.jpg',
            '/templates/panini/social-preview.jpg',
            '/templates/frontdesk/social-preview.jpg',
            '/templates/smart-inbox/social-preview.jpg',
            '/templates/sidekick/social-preview.jpg',
            '/templates/lemma-design/social-preview.jpg',
            '/templates/nachiketa/social-preview.jpg',
            '/templates/drop/social-preview.jpg',
            '/templates/meal/social-preview.jpg',
            '/templates/lemma-gtm/social-preview.jpg',
        ]);
    });

    it('rejects template sources outside GitHub', () => {
        const template = PUBLIC_TEMPLATES[0];
        expect(() =>
            templateRunHref({
                ...template,
                github: 'https://example.com/roundtable',
            }),
        ).toThrow('must use a github.com source');
    });
});
