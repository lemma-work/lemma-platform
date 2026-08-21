import { describe, expect, it } from 'vitest';
import { docsPageToMarkdown, legalDocumentToMarkdown } from './render';
import { PlayCircle } from '@/components/ui/icons';
import type { DocsPage } from '@/lib/data/docs';
import type { LegalDocument, PageDocument } from '@/lib/data/legal';

const page: DocsPage = {
    slug: 'example',
    title: 'Example Page',
    eyebrow: 'Test',
    group: 'Start',
    // Any real icon works — the icon isn't exercised by markdown rendering.
    icon: PlayCircle,
    description: 'A page used only to test the markdown renderer.',
    blocks: [
        { type: 'paragraph', title: 'Intro', body: 'Some intro text.' },
        { type: 'list', title: 'Bullets', items: ['One', 'Two'] },
        { type: 'steps', title: 'Steps', items: ['First', 'Second'] },
        { type: 'code', title: 'Snippet', language: 'bash', code: 'lemma pod create demo' },
        {
            type: 'table',
            title: 'Table',
            columns: ['A', 'B'],
            rows: [['1', '2']],
        },
        { type: 'callout', title: 'Note', body: 'Watch out.' },
    ],
};

describe('docsPageToMarkdown', () => {
    it('leads with an H1 and the description', () => {
        const markdown = docsPageToMarkdown(page);
        expect(markdown.startsWith('# Example Page\n\n')).toBe(true);
        expect(markdown).toContain('A page used only to test the markdown renderer.');
    });

    it('renders every block type as recognizable markdown', () => {
        const markdown = docsPageToMarkdown(page);
        expect(markdown).toContain('### Intro\n\nSome intro text.');
        expect(markdown).toContain('- One\n- Two');
        expect(markdown).toContain('1. First\n2. Second');
        expect(markdown).toContain('```bash\nlemma pod create demo\n```');
        expect(markdown).toContain('| A | B |');
        expect(markdown).toContain('| 1 | 2 |');
        expect(markdown).toContain('> **Note**\n>\n> Watch out.');
    });
});

const legalDoc: LegalDocument = {
    title: 'Sample Policy',
    description: 'A short policy used only for the renderer test.',
    effectiveDate: 'January 1, 2026',
    summary: ['First summary line.', 'Second summary line.'],
    sections: [
        {
            title: 'Section One',
            body: 'Body text.',
            items: [{ label: 'Label', text: 'Item text.', children: ['Child detail.'] }],
        },
    ],
};

describe('legalDocumentToMarkdown', () => {
    it('leads with an H1, description, and effective date', () => {
        const markdown = legalDocumentToMarkdown(legalDoc);
        expect(markdown.startsWith('# Sample Policy\n\n')).toBe(true);
        expect(markdown).toContain('A short policy used only for the renderer test.');
        expect(markdown).toContain('_Effective January 1, 2026_');
    });

    it('renders the summary, sections, and nested items', () => {
        const markdown = legalDocumentToMarkdown(legalDoc);
        expect(markdown).toContain('- First summary line.');
        expect(markdown).toContain('## Section One');
        expect(markdown).toContain('Body text.');
        expect(markdown).toContain('- **Label:** Item text.\n  - Child detail.');
    });

    it('renders a document with no effective date or summary, like About or Contact', () => {
        const minimal: PageDocument = {
            title: 'About',
            description: 'A page with only the fields it needs.',
            sections: [{ title: 'Only Section', body: 'Just this.' }],
        };
        const markdown = legalDocumentToMarkdown(minimal);
        expect(markdown).not.toContain('_Effective');
        expect(markdown).not.toContain('undefined');
        expect(markdown).toContain('## Only Section');
        expect(markdown).toContain('Just this.');
    });
});
