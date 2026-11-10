import test from 'node:test';
import assert from 'node:assert/strict';
import { MAX_REJOINS, REJOIN_DELAY_MS, rejoinAfter } from '../src/call/call-rejoin.ts';

test('a drop is joined again, waiting longer each time', () => {
    assert.equal(rejoinAfter(true, 0), REJOIN_DELAY_MS);
    assert.equal(rejoinAfter(true, 1), REJOIN_DELAY_MS * 2);
});

test('a call that dropped too often, or was refused or ended, is over', () => {
    assert.equal(rejoinAfter(true, MAX_REJOINS), null);
    assert.equal(rejoinAfter(false, 0), null);
});
