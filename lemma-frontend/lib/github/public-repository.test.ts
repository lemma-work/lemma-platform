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

    // The real gymmit README, reduced: its only line starting `# ` is a shell
    // comment inside a fence. That line became the page title on lemma.work, and
    // was then deleted out of the install instructions it belonged to.
    it('does not read a title out of a fenced code block', () => {
        const markdown = [
            '<p align="center">',
            '  <img src="./docs/demo.webp" alt="Demo" width="300" />',
            '</p>',
            '',
            '<h1 align="center">Gymmit</h1>',
            '',
            '## Run your own',
            '',
            '```bash',
            'cp apps/gymmit/source/.env.example apps/gymmit/source/.env.local',
            '# set VITE_LEMMA_API_URL, VITE_LEMMA_AUTH_URL, VITE_LEMMA_POD_ID',
            'npm run dev',
            '```',
        ].join('\n');

        const result = extractReadmePresentation(markdown, 'gymmit');

        expect(result.title).toBe('Gymmit');
        expect(result.body).toContain(
            '# set VITE_LEMMA_API_URL, VITE_LEMMA_AUTH_URL, VITE_LEMMA_POD_ID',
        );
        // The heading became the title, so it must not also be printed as body
        // text — `cleanReadmeBody` keeps the contents of tags it strips, which
        // is how a consumed `<h1>` surfaces as its own literal markup.
        expect(result.body).not.toContain('<h1');
        expect(result.body).not.toContain('Gymmit');
    });

    it('carries the width an author declared for the cover', () => {
        const result = extractReadmePresentation(
            '<img src="./docs/phone.png" alt="" width="300" />\n\n## Detail\n\nText.',
            'gymmit',
        );
        expect(result.coverImage).toBe('./docs/phone.png');
        expect(result.coverMaxWidth).toBe('300px');
    });

    // `width="100%"` is how a social banner asks for the whole column. Reading
    // the leading digits off it capped a 1280px banner at 100px.
    it('reads a percentage width as filling the column, not as pixels', () => {
        const result = extractReadmePresentation(
            '<p align="center">\n  <img src="./docs/social-preview.jpg" alt="" width="100%"/>\n</p>\n\n## Detail\n\nText.',
            'panini',
        );
        expect(result.coverImage).toBe('./docs/social-preview.jpg');
        expect(result.coverMaxWidth).toBe('100%');
    });

    it('leaves the cap to the caller when the width is a unit it cannot resolve', () => {
        expect(
            extractReadmePresentation('<img src="./a.png" width="20em" />', 'x').coverMaxWidth,
        ).toBeNull();
        expect(
            extractReadmePresentation('<img src="./a.png" />', 'x').coverMaxWidth,
        ).toBeNull();
        expect(
            extractReadmePresentation('<img src="./a.png" width="480px" />', 'x').coverMaxWidth,
        ).toBe('480px');
    });

    it('keeps a real markdown title even when a fence contains one too', () => {
        const markdown = [
            '```sh',
            '# not the title',
            '```',
            '',
            '# Research Desk',
            '',
            'Source-backed research for teams.',
        ].join('\n');

        const result = extractReadmePresentation(markdown, 'research-desk');

        expect(result.title).toBe('Research Desk');
        expect(result.body).toContain('# not the title');
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

    it('decodes each HTML entity once, so escaped markup stays escaped', () => {
        // Decoding `&amp;` first re-creates entities that the later passes then
        // decode a second time, turning text the author escaped on purpose back
        // into markup. The intro comes from the `<p>` preamble, which is the
        // path that decodes entities.
        const result = extractReadmePresentation(
            [
                '# Entities',
                '<p>To show a script tag in Markdown write &amp;lt;script&amp;gt; instead.</p>',
            ].join('\n\n'),
            'entities',
        );

        expect(result.intro).toContain('&lt;script&gt;');
        expect(result.intro).not.toContain('<script>');
    });
    it('reads the badge host from the URL rather than from anywhere in the string', () => {
        // `source.includes('shields.io')` is true of a URL that merely mentions
        // it, so a README could hide its own cover image by naming a badge host
        // in a query string -- and a real badge served from a lookalike host
        // would not be recognised at all.
        const markdown = [
            '# Project',
            '![cover](https://example.com/cover.png?ref=shields.io)',
            '<p align="center">A shared board where people and agents work together on things.</p>',
        ].join('\n\n');

        const result = extractReadmePresentation(markdown, 'project');

        expect(result.coverImage).toBe(
            'https://example.com/cover.png?ref=shields.io',
        );
    });

    it('strips nested HTML comments completely', () => {
        // One pass is not a strip: removing the inner `<!-- -->` splices the
        // outer one back together, so `<!--` survived into the rendered body.
        const markdown = [
            '# Project',
            '<p align="center">A shared board where people and agents work together on things.</p>',
            // Three ways a single pass leaves markup behind: removing the inner
            // comment splices a new opener out of its neighbours; `--!>` is a
            // comment terminator HTML accepts and a `-->`-only regex does not;
            // and removing a marker can splice another one.
            '<!-<!-- hidden -->-',
            '<!-- also hidden --!>',
            '<<!--!--',
            'Visible body text that should survive the clean.',
        ].join('\n\n');

        const result = extractReadmePresentation(markdown, 'project');

        expect(result.body).not.toContain('<!--');
        expect(result.body).not.toContain('-->');
        expect(result.body).not.toContain('--!>');
        expect(result.body).not.toContain('hidden');
        expect(result.body).toContain('Visible body text');
    });
});
