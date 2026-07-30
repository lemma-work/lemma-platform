import { describe, expect, it } from 'vitest';

import {
    buildPodShareCardCopy,
    buildPodShareCardSvg,
    podShareCardFilename,
    splitPodShareCardTitle,
} from './pod-share-card';

describe('pod share card', () => {
    it('keeps short pod names on one line', () => {
        expect(splitPodShareCardTitle('Research Desk')).toEqual(['Research Desk']);
    });

    it('wraps a long pod name into two bounded lines', () => {
        const lines = splitPodShareCardTitle(
            'Customer support intelligence and escalation operator',
        );

        expect(lines).toHaveLength(2);
        expect(lines.join(' ').endsWith('…')).toBe(true);
    });

    it('escapes user-provided text in the generated SVG', () => {
        const svg = buildPodShareCardSvg({
            podName: 'Research & <review>',
            repoUrl: 'https://github.com/lemma-work/research-desk',
        });

        expect(svg).toContain('Research &amp; &lt;review&gt;');
        expect(svg).not.toContain('Research & <review>');
        expect(svg).toContain('github.com/lemma-work/research-desk');
    });

    it('builds concise share copy and a stable file name', () => {
        expect(
            buildPodShareCardCopy({
                podName: 'Launch Room',
                repoUrl: 'https://github.com/lemma-work/launch-room',
            }),
        ).toBe(
            'Run Launch Room on Lemma.\n\nhttps://github.com/lemma-work/launch-room',
        );
        expect(podShareCardFilename('Launch Room')).toBe(
            'launch-room-share-card.png',
        );
    });
});
