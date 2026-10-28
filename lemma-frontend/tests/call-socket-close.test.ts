import test from 'node:test';
import assert from 'node:assert/strict';
import { closeOutcome } from '../src/call/call-socket.ts';

test('a normal end is neither an error nor a drop', () => {
    assert.deepEqual(closeOutcome(1000), { reason: null, dropped: false });
    assert.deepEqual(closeOutcome(1005), { reason: null, dropped: false });
});

test('the server ending or refusing the call is not rejoined', () => {
    for (const code of [4401, 4403, 4404, 4429, 4503]) {
        const outcome = closeOutcome(code);
        assert.equal(outcome.dropped, false, String(code));
        assert.ok(outcome.reason);
    }
});

test('a close nobody chose is a drop, and the same call is joined again', () => {
    assert.deepEqual(closeOutcome(1006), { reason: 'The call dropped.', dropped: true });
    assert.equal(closeOutcome(1011, 'server restarting').dropped, true);
});
