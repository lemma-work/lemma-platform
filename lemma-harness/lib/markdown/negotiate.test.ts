import { describe, expect, it } from 'vitest';
import { prefersMarkdown } from './negotiate';

describe('prefersMarkdown', () => {
    it('is false with no Accept header', () => {
        expect(prefersMarkdown(null)).toBe(false);
        expect(prefersMarkdown(undefined)).toBe(false);
        expect(prefersMarkdown('')).toBe(false);
    });

    it('is true when markdown is the only type requested', () => {
        expect(prefersMarkdown('text/markdown')).toBe(true);
    });

    it('is false for an ordinary browser Accept header', () => {
        expect(
            prefersMarkdown('text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8')
        ).toBe(false);
    });

    it('is false when html is explicitly preferred over markdown', () => {
        expect(prefersMarkdown('text/markdown;q=0.5, text/html;q=0.9')).toBe(false);
    });

    it('is true when markdown is preferred over html', () => {
        expect(prefersMarkdown('text/markdown;q=0.9, text/html;q=0.5')).toBe(true);
    });

    it('is true when markdown and html carry equal weight', () => {
        expect(prefersMarkdown('text/markdown, text/html')).toBe(true);
    });

    it('ignores unrelated types such as a bare wildcard', () => {
        expect(prefersMarkdown('*/*')).toBe(false);
        expect(prefersMarkdown('application/json')).toBe(false);
    });
});
