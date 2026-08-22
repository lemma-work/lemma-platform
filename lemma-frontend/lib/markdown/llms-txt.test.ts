import { describe, expect, it } from 'vitest';
import { llmsTxt } from './llms-txt';

describe('llmsTxt', () => {
    const output = llmsTxt();

    it('starts with an H1 naming the project, per llmstxt.org', () => {
        expect(output.startsWith('# Lemma\n')).toBe(true);
    });

    it('follows the H1 with a blockquote summary', () => {
        const lines = output.split('\n');
        const firstBlankIndex = lines.findIndex((line) => line.trim() === '');
        const nextLine = lines[firstBlankIndex + 1];
        expect(nextLine.startsWith('> ')).toBe(true);
    });

    it('names specific use cases, not generic marketing copy', () => {
        expect(output).toContain('## When to use Lemma');
        expect(output).toContain('Skip Lemma');
    });

    it('links the OpenAPI spec and llms.txt-adjacent developer resources', () => {
        expect(output).toContain('/openapi.json');
        expect(output).toContain('/docs');
    });

    it('puts secondary links under an Optional section', () => {
        expect(output).toContain('## Optional');
        const optionalIndex = output.indexOf('## Optional');
        expect(output.indexOf('/about')).toBeGreaterThan(optionalIndex);
    });
});
