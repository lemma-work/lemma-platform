import { describe, expect, it } from 'vitest';

import {
    extractReadmePresentation,
    resolveReadmeAssetUrl,
    resolveReadmeLinkUrl,
} from './public-repository';

describe('public GitHub README presentation', () => {
    it('uses repository copy and skips install badges when choosing a cover', () => {
        const markdown = `
<p align="center">
  <img src="./docs/social-preview.jpg" alt="Preview" width="100%"/>
</p>
<p align="center">
  <a href="https://lemma.work"><img src="./docs/install-remix-on-lemma.svg" alt="Install"/></a>
</p>
<p align="center">A shared task board where people and agents work together.</p>

## Why it exists

Useful detail.
        `;

        const result = extractReadmePresentation(markdown, 'roundtable');

        expect(result).toMatchObject({
            title: 'Roundtable',
            intro: 'A shared task board where people and agents work together.',
            coverImage: './docs/social-preview.jpg',
        });
        expect(result.body).toBe('## Why it exists\n\nUseful detail.');
    });

    it('prefers an explicit README title', () => {
        const result = extractReadmePresentation(
            '# Research Desk\n\nSource-backed research for teams.',
            'research-desk',
        );
        expect(result.title).toBe('Research Desk');
        expect(result.intro).toBe('Source-backed research for teams.');
        expect(result.body).not.toContain('# Research Desk');
    });

    it('resolves relative assets and links against the repository branch', () => {
        expect(resolveReadmeAssetUrl('./docs/hero.png', 'acme', 'desk', 'trunk')).toBe(
            'https://raw.githubusercontent.com/acme/desk/trunk/docs/hero.png',
        );
        expect(resolveReadmeLinkUrl('./docs/setup.md', 'acme', 'desk', 'trunk')).toBe(
            'https://github.com/acme/desk/blob/trunk/docs/setup.md',
        );
        expect(resolveReadmeLinkUrl('#setup', 'acme', 'desk', 'trunk')).toBe('#setup');
    });

    it('removes raw presentation HTML without touching fenced code', () => {
        const result = extractReadmePresentation(
            [
                '## Share',
                '<p><a href="https://example.com"><img src="share.svg" /></a></p>',
                '```html',
                '<p>Keep this example.</p>',
                '```',
            ].join('\n\n'),
            'example',
        );

        expect(result.body).not.toContain('href="https://example.com"');
        expect(result.body).toContain('<p>Keep this example.</p>');
    });
});
