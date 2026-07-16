import { describe, expect, it } from 'vitest';

import {
    buildScopedConversationHref,
    resolveConversationAgentName,
    resolveHydratedConversationRuntime,
    updateConversationAgentQuery,
} from '../conversation-composer-context';

describe('conversation composer context', () => {
    it('resolves a persisted agent id to its display resource name', () => {
        expect(resolveConversationAgentName('agent-2', [
            { id: 'agent-1', name: 'researcher' },
            { id: 'agent-2', name: 'roundtable_agent' },
        ])).toBe('roundtable_agent');
        expect(resolveConversationAgentName(null, [])).toBeNull();
        expect(resolveConversationAgentName('missing', [])).toBeNull();
    });

    it('uses the persisted runtime on reload instead of stale controller state', () => {
        expect(resolveHydratedConversationRuntime({
            isNewConversation: false,
            hasPersistedConversation: true,
            persistedRuntime: { profile_id: 'system:lemma', model_name: 'glm-5.2' },
            controllerRuntime: { profile_id: 'system:lemma', model_name: 'stale-model' },
        })).toEqual({ profile_id: 'system:lemma', model_name: 'glm-5.2' });
        expect(resolveHydratedConversationRuntime({
            isNewConversation: false,
            hasPersistedConversation: true,
            persistedRuntime: null,
            controllerRuntime: { profile_id: 'system:lemma', model_name: 'stale-model' },
        })).toBeNull();
    });

    it('changes only the agent scope in an existing new-conversation query', () => {
        expect(updateConversationAgentQuery('assistantMessage=hello&mode=fast', 'roundtable_agent'))
            .toBe('assistantMessage=hello&mode=fast&agent=roundtable_agent');
        expect(updateConversationAgentQuery('mode=fast&agent=roundtable_agent', null))
            .toBe('mode=fast');
    });

    it('preserves named-agent scope after the conversation is created', () => {
        expect(buildScopedConversationHref({
            podId: 'pod/one',
            conversationId: 'conversation one',
            agentName: 'roundtable agent',
        })).toBe('/pod/pod%2Fone/conversations/conversation%20one?agent=roundtable%20agent');
        expect(buildScopedConversationHref({
            podId: 'pod-one',
            conversationId: 'conversation-one',
            agentName: null,
        })).toBe('/pod/pod-one/conversations/conversation-one');
    });
});
