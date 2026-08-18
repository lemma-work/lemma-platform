import { describe, expect, it } from 'vitest';

import {
    buildComposerLaunchHref,
    parseConversationMetadataParam,
    readComposerLaunch,
    stripAssistantLaunchParams,
    stripComposerLaunchParams,
} from './composer-launch';

function queryOf(href: string) {
    return new URLSearchParams(href.slice(href.indexOf('?') + 1));
}

describe('composer launch', () => {
    it('round-trips a draft, its instructions, and its metadata', () => {
        const href = buildComposerLaunchHref('pod-1', {
            draft: 'Build a Telegram agent and companion app that ',
            instructions: 'Treat the message as the agent instructions.',
            metadata: { source: 'create_screen', intent: 'telegram_agent_companion_app' },
        });

        expect(href.startsWith('/pod/pod-1?')).toBe(true);
        expect(readComposerLaunch(queryOf(href))).toEqual({
            draft: 'Build a Telegram agent and companion app that ',
            instructions: 'Treat the message as the agent instructions.',
            metadata: { source: 'create_screen', intent: 'telegram_agent_companion_app' },
        });
    });

    it('keeps the trailing space that puts the caret mid-sentence', () => {
        const href = buildComposerLaunchHref('pod-1', { draft: 'Build an app that ' });

        // URLSearchParams encodes a trailing space as "+", which decodes back.
        // Losing it would leave the caret jammed against the last word.
        expect(readComposerLaunch(queryOf(href))?.draft).toBe('Build an app that ');
    });

    it('is nothing to seed when the pod was opened normally', () => {
        expect(readComposerLaunch(new URLSearchParams(''))).toBeNull();
        expect(readComposerLaunch(new URLSearchParams('tab=build'))).toBeNull();
    });

    it('strips only its own params, so the rest of the URL survives', () => {
        const params = new URLSearchParams(
            'tab=build&composerDraft=hello&conversationInstructions=framing&conversationMetadata=%7B%7D',
        );

        expect(stripComposerLaunchParams(params)).toBe('tab=build');
        // The source must not be mutated — the effect still reads it afterward.
        expect(params.get('composerDraft')).toBe('hello');
    });

    it('strips a send-on-arrival launch without taking the route scope with it', () => {
        const params = new URLSearchParams(
            'agent=support&assistantMessage=Hi&conversationInstructions=framing&conversationMetadata=%7B%7D',
        );

        // `agent=` is what the route runs as, not part of the launch: dropping
        // it would hand the message to the pod default instead.
        expect(stripAssistantLaunchParams(params)).toBe('agent=support');
        expect(params.get('assistantMessage')).toBe('Hi');
    });

    it('leaves a composer launch alone, and the reverse', () => {
        const composer = new URLSearchParams('composerDraft=hello');
        const assistant = new URLSearchParams('assistantMessage=Hi');

        expect(stripAssistantLaunchParams(composer)).toBe('composerDraft=hello');
        expect(stripComposerLaunchParams(assistant)).toBe('assistantMessage=Hi');
    });

    it('drops metadata it cannot trust rather than failing the navigation', () => {
        expect(parseConversationMetadataParam('not json')).toBeNull();
        expect(parseConversationMetadataParam('[1,2]')).toBeNull();
        expect(parseConversationMetadataParam('"a string"')).toBeNull();
        expect(parseConversationMetadataParam(null)).toBeNull();
        expect(parseConversationMetadataParam('{"pod_id":"p1"}')).toEqual({ pod_id: 'p1' });
    });

    it('still seeds framing when a path carries instructions but no sentence', () => {
        const href = buildComposerLaunchHref('pod-1', { draft: '', instructions: 'framing' });

        expect(readComposerLaunch(queryOf(href))).toEqual({
            draft: '',
            instructions: 'framing',
            metadata: undefined,
        });
    });
});
