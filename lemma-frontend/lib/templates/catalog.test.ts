import { describe, expect, it } from 'vitest';

import { getPublicTemplateBySlug, PUBLIC_TEMPLATES, templateRunHref } from './catalog';

describe('public template catalogue', () => {
    it('hides entries until a verified public bundle is published', () => {
        expect(PUBLIC_TEMPLATES).toEqual([]);
        expect(getPublicTemplateBySlug('research-desk')).toBeNull();
    });

    it('rejects template sources outside GitHub', () => {
        expect(() =>
            templateRunHref({
                slug: 'example',
                name: 'Example',
                kicker: 'Example',
                description: 'Example',
                outcomes: [],
                includes: [],
                github: 'https://example.com/research-desk',
            }),
        ).toThrow('must use a github.com source');
    });
});
