import { describe, expect, it } from 'vitest';

import {
    buildPodConversationHref,
    buildPodConversationsHref,
    findConversationAgentName,
    getConversationRouteId,
} from '../conversations';

describe('conversation route helpers', () => {
    it('preserves an agent scope on list, existing, and new conversation routes', () => {
        expect(buildPodConversationsHref('pod-1', 'research agent')).toBe(
            '/pod/pod-1/conversations?agent=research+agent',
        );
        expect(buildPodConversationHref('pod-1', 'conversation-1', 'research agent')).toBe(
            '/pod/pod-1/conversations/conversation-1?agent=research+agent',
        );
        expect(buildPodConversationHref('pod-1', 'new', 'research agent')).toBe(
            '/pod/pod-1/conversations/new?agent=research+agent',
        );
    });

    it('keeps pod-default conversation routes free of agent query parameters', () => {
        expect(buildPodConversationsHref('pod-1')).toBe('/pod/pod-1/conversations');
        expect(buildPodConversationHref('pod-1', 'conversation-1')).toBe(
            '/pod/pod-1/conversations/conversation-1',
        );
    });

    it('extracts only existing conversation ids from pod conversation routes', () => {
        expect(getConversationRouteId('/pod/pod-1/conversations/conversation%201')).toBe('conversation 1');
        expect(getConversationRouteId('/pod/pod-1/conversations/new')).toBeNull();
        expect(getConversationRouteId('/pod/pod-1/conversations')).toBeNull();
        expect(getConversationRouteId('/pod/pod-1/agents/king')).toBeNull();
    });

    it('maps a conversation agent id back to the agent name used for controller scope', () => {
        const agents = [
            { id: 'agent-1', name: 'writer' },
            { id: 'agent-2', name: 'king' },
        ];

        expect(findConversationAgentName('agent-2', agents)).toBe('king');
        expect(findConversationAgentName('deleted-agent', agents)).toBeNull();
        expect(findConversationAgentName(null, agents)).toBeNull();
    });
});
