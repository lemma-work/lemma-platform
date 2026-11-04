import test from 'node:test';
import assert from 'node:assert/strict';
import { isCallConversation } from '../src/call/call-conversation.ts';

test('a call\'s own conversation is recognised by its metadata', () => {
    assert.equal(isCallConversation({ metadata: { call: { call_id: 'c', role: 'coordinator' }, cwd: '/x' } }), true);
});

test('an ordinary chat, or one that could not be read, is not a call', () => {
    assert.equal(isCallConversation({ metadata: { cwd: '/x' } }), false);
    assert.equal(isCallConversation({ metadata: null }), false);
    assert.equal(isCallConversation(null), false);
});
