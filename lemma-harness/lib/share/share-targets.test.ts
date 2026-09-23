import { describe, expect, it } from 'vitest';

import {
    buildShareClipboardText,
    buildShareText,
    buildShareTitle,
    getShareTarget,
    SHARE_TARGETS,
} from './share-targets';

const subject = {
    name: 'Research Desk',
    url: 'https://lemma.work/import/github/acme/research-desk',
    summary: 'Reads the inbox, drafts the reply, files the thread.',
};

describe('share targets', () => {
    it('writes a post rather than a bare link', () => {
        expect(buildShareTitle(subject)).toBe('Run Research Desk on Lemma');
        expect(buildShareText(subject)).toBe(
            'Run Research Desk on Lemma. Reads the inbox, drafts the reply, files the thread.',
        );
        expect(buildShareClipboardText(subject)).toBe(
            `Run Research Desk on Lemma. Reads the inbox, drafts the reply, files the thread.\n\n${subject.url}`,
        );
    });

    it('falls back to a generic subject when the name is missing', () => {
        expect(buildShareTitle({ url: subject.url })).toBe('Run this pod on Lemma');
    });

    it('bounds the post so a link never gets truncated', () => {
        const text = buildShareText({ ...subject, summary: 'x'.repeat(400) });
        expect(text.length).toBe(200);
        expect(text.endsWith('…')).toBe(true);
    });

    it('encodes every intent url', () => {
        for (const target of SHARE_TARGETS) {
            const href = target.href(subject);
            expect(href).not.toContain(' ');
            expect(() => new URL(href)).not.toThrow();
        }
    });

    it('keeps every chat destination people actually forward links in', () => {
        expect(SHARE_TARGETS.map((target) => target.id)).toEqual([
            'x',
            'linkedin',
            'whatsapp',
            'telegram',
            'reddit',
            'email',
        ]);

        const telegram = new URL(getShareTarget('telegram').href(subject));
        expect(telegram.searchParams.get('url')).toBe(subject.url);
        expect(telegram.searchParams.get('text')).toContain('Run Research Desk on Lemma');
    });

    it('sends the link to X and lets LinkedIn read the card from Open Graph', () => {
        const x = new URL(getShareTarget('x').href(subject));
        expect(x.searchParams.get('url')).toBe(subject.url);
        expect(x.searchParams.get('text')).toContain('Run Research Desk on Lemma');

        const linkedin = new URL(getShareTarget('linkedin').href(subject));
        expect(linkedin.searchParams.get('url')).toBe(subject.url);
        expect(linkedin.searchParams.get('text')).toBeNull();
    });

    it('rejects an unknown target instead of returning undefined', () => {
        // @ts-expect-error — guarding the runtime path a bad id would take.
        expect(() => getShareTarget('myspace')).toThrow(/Unknown share target/);
    });
});
