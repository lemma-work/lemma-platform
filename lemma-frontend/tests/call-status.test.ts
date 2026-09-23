import test from 'node:test';
import assert from 'node:assert/strict';
import { statusLine } from '../src/call/call-status.ts';
import type { PlanStepState } from '../src/thread/turns.ts';

const plan = (...statuses: PlanStepState['status'][]): PlanStepState[] =>
    statuses.map((status, i) => ({ step: 'step ' + (i + 1), status }));

test('an error is the only thing worth saying when there is one', () => {
    assert.equal(
        statusLine({ error: 'The call dropped.', muted: true, plan: plan('in_progress'), thinking: true, teammate: 'Marketing' }),
        'The call dropped.',
    );
});

test('muted outranks the plan, because it is about whether the call works at all', () => {
    assert.equal(
        statusLine({ muted: true, plan: plan('completed', 'in_progress'), teammate: 'Marketing' }),
        'Muted — it cannot hear you',
    );
});

test('an open step is named and counted', () => {
    assert.equal(
        statusLine({ plan: plan('completed', 'completed', 'in_progress', 'pending'), teammate: 'Marketing' }),
        'Step 3 of 4 · step 3',
    );
});

test('a finished plan stops claiming work is in progress', () => {
    // The failure this prevents: a plan that has closed every step going on
    // announcing a step long after the run ended.
    assert.equal(
        statusLine({ plan: plan('completed', 'completed'), thinking: false, teammate: 'Marketing' }),
        'Just talk. It is listening.',
    );
});

test('a plan with no step open yet still says where the work is, while it runs', () => {
    // Agents that write the whole plan up front, or close each step before
    // opening the next, leave stretches with nothing in progress. Requiring
    // one blanked the line in the middle of the work it describes.
    assert.equal(
        statusLine({ plan: plan('pending', 'pending'), thinking: true, teammate: 'Marketing' }),
        'Step 1 of 2 · step 1',
    );
    assert.equal(
        statusLine({ plan: plan('completed', 'pending', 'pending'), thinking: true, teammate: 'Marketing' }),
        'Step 2 of 3 · step 2',
    );
});

test('a plan only stands in for a running turn, never a finished one', () => {
    // Otherwise a half-done plan from the last question keeps announcing a
    // step while the call sits idle.
    assert.equal(
        statusLine({ plan: plan('completed', 'pending'), thinking: false, teammate: 'Marketing' }),
        'Just talk. It is listening.',
    );
    // An open step is authoritative and says itself either way.
    assert.equal(
        statusLine({ plan: plan('completed', 'in_progress'), thinking: false, teammate: 'Marketing' }),
        'Step 2 of 2 · step 2',
    );
});

test('no plan at all is a normal call, not an empty one', () => {
    assert.equal(statusLine({ teammate: 'Marketing' }), 'Just talk. It is listening.');
    assert.equal(statusLine({ plan: [], teammate: 'Marketing' }), 'Just talk. It is listening.');
});
