import { describe, expect, it } from 'vitest';

import {
    buildConversationPresentationHref,
    buildConversationStageEmbedHref,
    buildConversationStandaloneResourceHref,
    normalizeConversationPresentedResourceHref,
    removeConversationPresentationParam,
} from '../conversation-presentation';

describe('conversation presentation routes', () => {
    it('keeps the conversation route canonical while presenting a resource', () => {
        expect(buildConversationPresentationHref({
            pathname: '/pod/p1/conversations/c1',
            searchParams: 'agent=researcher',
            resourceHref: '/pod/p1/files?file=%2Fbrief.md&assistantConversationId=c1',
            activeConversationId: 'c1',
        })).toBe(
            '/pod/p1/conversations/c1?agent=researcher&presented=%2Fpod%2Fp1%2Ffiles%3Ffile%3D%252Fbrief.md%26assistantConversationId%3Dc1',
        );
    });

    it('promotes a new route to the created conversation before presenting', () => {
        expect(buildConversationPresentationHref({
            pathname: '/pod/p1/conversations/new',
            searchParams: '',
            resourceHref: '/pod/p1/widgets/view?toolCallId=t1&assistantConversationId=c2',
            activeConversationId: 'c2',
        })).toBe(
            '/pod/p1/conversations/c2?presented=%2Fpod%2Fp1%2Fwidgets%2Fview%3FtoolCallId%3Dt1%26assistantConversationId%3Dc2',
        );
    });

    it('only accepts non-conversation resource routes from the same pod', () => {
        expect(normalizeConversationPresentedResourceHref('/pod/p1/data?tab=orders', 'p1'))
            .toBe('/pod/p1/data?tab=orders');
        expect(normalizeConversationPresentedResourceHref('/pod/p2/data?tab=orders', 'p1')).toBeNull();
        expect(normalizeConversationPresentedResourceHref('/pod/p1/conversations/c2', 'p1')).toBeNull();
        expect(normalizeConversationPresentedResourceHref('https://example.com', 'p1')).toBeNull();
    });

    it('embeds widgets without reopening the global assistant', () => {
        expect(buildConversationStageEmbedHref(
            '/pod/p1/widgets/view?toolCallId=t1&assistantConversationId=c1',
        )).toBe(
            '/pod/p1/widgets/view?toolCallId=t1&conversationId=c1&embed=conversation-stage',
        );
    });

    it('opens widgets standalone with their tool context but no side assistant', () => {
        expect(buildConversationStandaloneResourceHref(
            '/pod/p1/widgets/view?toolCallId=t1&assistantConversationId=c1&embed=conversation-stage',
        )).toBe(
            '/pod/p1/widgets/view?toolCallId=t1&conversationId=c1&standalone=1',
        );
    });

    it('removes only the presentation state when returning to chat', () => {
        expect(removeConversationPresentationParam('agent=researcher&presented=%2Fpod%2Fp1%2Fdata'))
            .toBe('agent=researcher');
    });
});
