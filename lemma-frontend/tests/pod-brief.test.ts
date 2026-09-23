import test from 'node:test';
import assert from 'node:assert/strict';
import { podBrief, openCallConversation } from '../src/call/pod-brief.ts';
import { NEW_CONVERSATION } from '../src/data/types.ts';
import type { Profile } from '../src/data/types.ts';

function profileOf(over: Partial<Profile> = {}): Profile {
    return {
        podId: 'pod', name: 'Marketing', iconUrl: null, headline: '', joined: '',
        about: '', skills: [], permits: [], commitments: [], projects: [],
        counts: { tables: 0, functions: 0, workflows: 0 },
        ...over,
    };
}

test('a bare pod is described by what it is, never by what it lacks', () => {
    const brief = podBrief(profileOf(), 'Marketing');
    // The two things that are always true — where you are, and whose pod it
    // is — and nothing else. A pod with no schedules must not be described as
    // having none; a heading with an empty list under it is the shape a model
    // fills in for itself.
    assert.match(brief, /Lemma is a workspace/);
    assert.match(brief, /"Marketing"/);
    assert.doesNotMatch(brief, /Standing work|Apps it has built|What it can actually do|It holds/);
});

test('counts are said exactly, and a zero is simply absent', () => {
    const brief = podBrief(profileOf({ counts: { tables: 1, functions: 0, workflows: 12 } }), 'Marketing');
    assert.match(brief, /It holds 1 data table, 12 workflows\./);
    assert.doesNotMatch(brief, /function/);
});

test('retired standing work is not read out as current', () => {
    const brief = podBrief(profileOf({ commitments: [
        { id: 'a', title: 'Morning digest', detail: '', cadence: 'Every weekday at 09:00', since: '', active: true, last: '' },
        { id: 'b', title: 'Old cleanup', detail: '', cadence: 'Nightly', since: '', active: false, last: '' },
    ] }), 'Marketing');
    assert.match(brief, /Morning digest \(Every weekday at 09:00\)/);
    assert.doesNotMatch(brief, /Old cleanup/);
});

test('a pod that writes an essay about itself still fits in a voice session', () => {
    const brief = podBrief(profileOf({
        about: 'x'.repeat(40_000),
        headline: 'y'.repeat(4_000),
        skills: Array.from({ length: 60 }, (_, i) => ({ id: String(i), label: 'skill-' + i, blurb: '' })),
        projects: Array.from({ length: 40 }, (_, i) => ({ id: String(i), name: 'app-' + i, description: '', status: '', tabId: '' })),
    }), 'Marketing');
    assert.ok(brief.length < 2600, `brief was ${brief.length} characters`);
    // Trimmed from the bottom, so the part that says where you are survives.
    assert.match(brief, /Lemma is a workspace/);
});


test('a call reuses the open conversation without creating a child', async () => {
    const calls: unknown[] = [];
    const client = { conversations: {
        get: async (id: string, options: unknown) => { calls.push([id, options]); return { id }; },
        create: async () => { throw new Error('must not create'); },
    } };
    assert.equal((await openCallConversation(client, 'pod-1', 'conv-9')).id, 'conv-9');
    assert.deepEqual(calls, [['conv-9', { pod_id: 'pod-1' }]]);
});

test('an empty or new pane creates a regular main conversation', async () => {
    for (const id of [null, NEW_CONVERSATION]) {
        const payloads: unknown[] = [];
        const client = { conversations: {
            get: async () => { throw new Error('must not read a placeholder'); },
            create: async (payload: unknown) => { payloads.push(payload); return { id: 'main' }; },
        } };
        assert.equal((await openCallConversation(client, 'pod-1', id)).id, 'main');
        assert.deepEqual(payloads, [{ pod_id: 'pod-1' }]);
    }
});

test('an unreadable main conversation fails instead of silently forking', async () => {
    const client = { conversations: {
        get: async () => { throw new Error('access denied'); },
        create: async () => { throw new Error('must not create'); },
    } };
    await assert.rejects(openCallConversation(client, 'pod-1', 'conv-9'), /access denied/);
});
