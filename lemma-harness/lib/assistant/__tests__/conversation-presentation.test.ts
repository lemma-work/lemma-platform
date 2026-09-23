import { describe, expect, it } from 'vitest';

import {
    buildConversationPresentationHref,
    buildConversationStageEmbedHref,
    buildConversationStageNavigationMessage,
    buildConversationStandaloneResourceHref,
    buildResourceShareUrl,
    conversationStageAppSlug,
    normalizeConversationPresentedResourceHref,
    removeConversationPresentationParam,
    resolveConversationStageNavigationHref,
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

    it('promotes an embedded conversation navigation into the parent pod workspace', () => {
        expect(resolveConversationStageNavigationHref(
            buildConversationStageNavigationMessage(
                '/pod/p1/conversations/new?agent=researcher&assistantMessage=hello&embed=conversation-stage',
            ),
            'p1',
        )).toBe('/pod/p1/conversations/new?agent=researcher&assistantMessage=hello');
    });

    it('rejects presentation-stage navigation outside conversations in the current pod', () => {
        expect(resolveConversationStageNavigationHref(
            buildConversationStageNavigationMessage('/pod/p1/agents/researcher'),
            'p1',
        )).toBeNull();
        expect(resolveConversationStageNavigationHref(
            buildConversationStageNavigationMessage('/pod/p2/conversations/new'),
            'p1',
        )).toBeNull();
        expect(resolveConversationStageNavigationHref({ type: 'unknown', href: '/pod/p1/conversations/new' }, 'p1'))
            .toBeNull();
    });

    it('builds canonical share URLs without internal presentation state', () => {
        expect(buildResourceShareUrl(
            '/pod/p1/data?tab=projects&embed=conversation-stage&assistant=docked&presentation=side',
            'http://localhost:3000',
        )).toBe('http://localhost:3000/pod/p1/data?tab=projects');
    });

    it('names the app a presented resource points at, so the stage skips the frame', () => {
        expect(conversationStageAppSlug('/pod/p1/app/view?page=ledger', 'p1')).toBe('ledger');
        expect(conversationStageAppSlug(
            '/pod/p1/app/view?page=ledger&assistantConversationId=c1',
            'p1',
        )).toBe('ledger');
    });

    it('canonicalizes a link that names the app rather than its page', () => {
        // Links reach the stage from outside the workspace — a chat surface, an
        // agent's own deep link — carrying the app's resource name. The index
        // slugs its pages, so a name is normalized to the slug it maps to
        // instead of missing the page and reporting the app unavailable.
        expect(conversationStageAppSlug('/pod/p1/app/view?page=Expense%20Tracker', 'p1'))
            .toBe('expense-tracker');
        expect(conversationStageAppSlug('/pod/p1/app/view?page=Expense+Tracker', 'p1'))
            .toBe('expense-tracker');
        expect(conversationStageAppSlug('/pod/p1/app/view?page=Quote_Desk', 'p1'))
            .toBe('quote-desk');
    });

    it('only treats a same-pod app view with a slug as an app', () => {
        // The apps index, and a view route that lost its slug, are pages of their
        // own — framing them is still correct, so they must not read as an app.
        expect(conversationStageAppSlug('/pod/p1/app/pages', 'p1')).toBeNull();
        expect(conversationStageAppSlug('/pod/p1/app/view', 'p1')).toBeNull();
        expect(conversationStageAppSlug('/pod/p1/app/view?page=', 'p1')).toBeNull();
        expect(conversationStageAppSlug('/pod/p1/app/view?page=%20', 'p1')).toBeNull();
        expect(conversationStageAppSlug('/pod/p2/app/view?page=ledger', 'p1')).toBeNull();
        expect(conversationStageAppSlug('/pod/p1/data?tab=orders', 'p1')).toBeNull();
        expect(conversationStageAppSlug('https://example.com/pod/p1/app/view?page=ledger', 'p1')).toBeNull();
    });

    it('removes only the presentation state when returning to chat', () => {
        expect(removeConversationPresentationParam('agent=researcher&presented=%2Fpod%2Fp1%2Fdata'))
            .toBe('agent=researcher');
    });
});
