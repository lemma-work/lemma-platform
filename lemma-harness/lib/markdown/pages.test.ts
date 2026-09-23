import { describe, expect, it } from 'vitest';
import { markdownForPath } from './pages';

describe('markdownForPath', () => {
    it('renders the homepage', () => {
        const markdown = markdownForPath('/');
        expect(markdown).toContain('# Lemma');
    });

    it('renders the docs index', () => {
        const markdown = markdownForPath('/docs');
        expect(markdown).toContain('# Lemma Docs');
    });

    it('renders a known docs page by slug', () => {
        const markdown = markdownForPath('/docs/getting-started');
        expect(markdown).toContain('# Quickstart');
    });

    it('renders privacy, terms, about, and contact', () => {
        expect(markdownForPath('/privacy')).toContain('# Privacy');
        expect(markdownForPath('/tos')).toContain('# Terms of Service');
        expect(markdownForPath('/about')).toContain('# About Lemma');
        expect(markdownForPath('/contact')).toContain('# Contact');
    });

    it('returns null for an unknown docs slug or an unnegotiated route', () => {
        expect(markdownForPath('/docs/not-a-real-page')).toBeNull();
        expect(markdownForPath('/pod/some-id')).toBeNull();
    });
});
