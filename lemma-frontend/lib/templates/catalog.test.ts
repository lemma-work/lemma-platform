import { describe, expect, it } from 'vitest';

import { getPublicTemplateBySlug, templateRunHref } from './catalog';

describe('public template catalogue', () => {
    it('routes Research Desk through the GitHub bundle installer', () => {
        const template = getPublicTemplateBySlug('research-desk');
        expect(template).not.toBeNull();
        expect(templateRunHref(template!)).toBe(
            '/import/github/lemma-work/research-desk',
        );
    });

    it('rejects template sources outside GitHub', () => {
        const template = getPublicTemplateBySlug('research-desk');
        expect(template).not.toBeNull();
        expect(() =>
            templateRunHref({
                ...template!,
                github: 'https://example.com/research-desk',
            }),
        ).toThrow('must use a github.com source');
    });
});
