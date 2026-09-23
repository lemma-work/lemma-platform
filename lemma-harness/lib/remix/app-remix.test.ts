import { describe, expect, it } from 'vitest';

import {
    buildAppRemixConversationHref,
    buildAppRemixPrompt,
    buildCreatePodForRemixHref,
    normalizeRemixSource,
    remixSourceLabel,
} from './app-remix';

describe('app remix links', () => {
    it('accepts only bounded http sources', () => {
        expect(normalizeRemixSource('https://research.apps.lemma.work')).toBe(
            'https://research.apps.lemma.work/',
        );
        expect(normalizeRemixSource('javascript:alert(1)')).toBeNull();
        expect(normalizeRemixSource('not a url')).toBeNull();
        expect(normalizeRemixSource(`https://example.com/${'x'.repeat(2_100)}`)).toBeNull();
    });

    it('creates a new assistant conversation with durable remix context', () => {
        const source = 'https://research.apps.lemma.work/';
        const href = buildAppRemixConversationHref('pod 1', source);
        const url = new URL(href, 'https://lemma.work');

        expect(url.pathname).toBe('/pod/pod%201/conversations/new');
        expect(url.searchParams.get('assistantMessage')).toBe(buildAppRemixPrompt(source));
        expect(url.searchParams.get('conversationInstructions')).toContain(
            'Remix on Lemma',
        );
        expect(JSON.parse(url.searchParams.get('conversationMetadata') || '{}')).toEqual({
            source: 'public_app_remix',
            source_url: source,
        });
    });

    it('builds readable labels and preserves the source through pod creation', () => {
        const source = 'https://www.example.com/app';
        expect(remixSourceLabel(source)).toBe('example.com');

        const href = buildCreatePodForRemixHref(source);
        const url = new URL(href, 'https://lemma.work');
        expect(url.pathname).toBe('/create-pod');
        expect(url.searchParams.get('remixSource')).toBe(source);
    });
});
