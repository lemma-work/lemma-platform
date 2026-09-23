import { describe, expect, it } from 'vitest';

import {
    buildFrontmatter,
    joinFrontmatter,
    setFrontmatterField,
    splitFrontmatter,
} from './frontmatter';

const WIDGET_SKILL = `---
name: lemma-widget
description: "Create lightweight inline Lemma widgets for conversations via display_resource(type=\\"WIDGET\\"): self-contained HTML/CSS/JS or SVG."
---

# Lemma Widget

A widget is the default way to show an answer.
`;

describe('splitFrontmatter', () => {
    it('keeps the block out of the body so the renderer never sees it', () => {
        const parsed = splitFrontmatter(WIDGET_SKILL);

        expect(parsed.fields.name).toBe('lemma-widget');
        expect(parsed.body.startsWith('# Lemma Widget')).toBe(true);
        expect(parsed.body).not.toContain('---');
    });

    it('unescapes quotes inside a quoted value', () => {
        const parsed = splitFrontmatter(WIDGET_SKILL);

        expect(parsed.fields.description).toContain('display_resource(type="WIDGET")');
        expect(parsed.fields.description).not.toContain('\\"');
    });

    it('leaves a file without frontmatter untouched', () => {
        const parsed = splitFrontmatter('# Notes\n\nJust a doc.\n');

        expect(parsed.raw).toBeNull();
        expect(parsed.body).toBe('# Notes\n\nJust a doc.\n');
    });

    it('treats an unclosed block as ordinary content rather than eating the file', () => {
        const parsed = splitFrontmatter('---\nname: broken\n\n# Body\n');

        expect(parsed.raw).toBeNull();
        expect(parsed.body).toContain('name: broken');
    });

    it('ignores comments, blank lines, and indented continuations', () => {
        const parsed = splitFrontmatter(
            '---\n# a comment\n\nname: demo\nallowed-tools:\n  - Read\n  - Write\n---\n\nBody\n'
        );

        expect(parsed.fields).toEqual({ name: 'demo', 'allowed-tools': '' });
    });

    it('leaves a doc that merely opens with a rule in the body', () => {
        const parsed = splitFrontmatter('---\nA pull quote\n---\n\n# Notes\n');

        expect(parsed.raw).toBeNull();
        expect(parsed.body).toContain('A pull quote');
    });

    it('splits on the first colon so values may contain their own', () => {
        const parsed = splitFrontmatter('---\ndescription: Use this: always\n---\n\nBody\n');

        expect(parsed.fields.description).toBe('Use this: always');
    });
});

describe('joinFrontmatter', () => {
    it('round-trips a skill back into something the loader still accepts', () => {
        const parsed = splitFrontmatter(WIDGET_SKILL);
        const rejoined = joinFrontmatter(parsed.raw, parsed.body);

        expect(rejoined.startsWith('---\nname: lemma-widget\n')).toBe(true);
        expect(splitFrontmatter(rejoined).fields).toEqual(parsed.fields);
    });

    it('returns the body alone when there was no block', () => {
        expect(joinFrontmatter(null, '# Notes\n')).toBe('# Notes\n');
    });

    /**
     * The editor is handed the body and hands one back; the viewer re-attaches
     * the block and re-splits it on the next render. If that trip changed the
     * body by so much as a newline, the editor would reset its own content in a
     * loop, so this is the invariant the wiring rests on.
     */
    it('is a fixed point, so the editor never fights its own output', () => {
        const raw = '---\nname: demo\ndescription: "Does a thing"\n---';

        for (const body of ['# Title\n\nProse.', '- one\n- two', '---\n\n# After a rule', '']) {
            expect(splitFrontmatter(joinFrontmatter(raw, body)).body).toBe(body);
        }
    });
});

describe('setFrontmatterField', () => {
    it('rewrites one key and leaves unknown keys where the author put them', () => {
        const raw = '---\nname: demo\nlicense: MIT\n---';
        const next = setFrontmatterField(raw, 'name', 'renamed');

        expect(next).toBe('---\nname: renamed\nlicense: MIT\n---');
    });

    it('appends a key the file did not have', () => {
        const next = setFrontmatterField('---\nname: demo\n---', 'description', 'Does a thing');

        expect(splitFrontmatter(`${next}\n\nBody`).fields.description).toBe('Does a thing');
    });

    it('quotes a value carrying a colon, and survives a read back', () => {
        const next = setFrontmatterField('---\nname: demo\n---', 'description', 'Use when: always');

        expect(next).toContain('description: "Use when: always"');
        expect(splitFrontmatter(`${next}\n\nBody`).fields.description).toBe('Use when: always');
    });

    it('escapes quotes so a description with one still parses', () => {
        const next = setFrontmatterField('---\nname: demo\n---', 'description', 'Call display_resource(type="WIDGET"): yes');

        expect(splitFrontmatter(`${next}\n\nBody`).fields.description)
            .toBe('Call display_resource(type="WIDGET"): yes');
    });

    it('collapses a pasted newline that would otherwise break the block', () => {
        const next = setFrontmatterField('---\nname: demo\n---', 'description', 'First line\nsecond line');

        expect(splitFrontmatter(`${next}\n\nBody`).fields.description).toBe('First line second line');
    });

    it('builds a block when the file had none', () => {
        expect(setFrontmatterField(null, 'name', 'demo')).toBe('---\nname: demo\n---');
    });
});

describe('buildFrontmatter', () => {
    it('writes a block the parser reads back unchanged', () => {
        const raw = buildFrontmatter({ name: 'weekly-report', description: 'Summarize the week: every Monday' });

        expect(splitFrontmatter(`${raw}\n\nBody`).fields).toEqual({
            name: 'weekly-report',
            description: 'Summarize the week: every Monday',
        });
    });
});
