import test from "node:test";
import assert from "node:assert/strict";
import {
    CONNECTED,
    afterProbe,
    healthAnswered,
    isTransportFailure,
    probeDelay,
    suspect,
} from "../src/shell/connection.ts";

test("only a failure that got no answer from the server starts a look", () => {
    assert.equal(isTransportFailure(new TypeError("Failed to fetch")), true);
    assert.equal(isTransportFailure({ name: "NetworkError" }), true);
    assert.equal(isTransportFailure({ statusCode: 502 }), true);
    assert.equal(isTransportFailure({ status: 503 }), true);
    // The server answering, even to refuse, is the server being up.
    assert.equal(isTransportFailure({ statusCode: 401 }), false);
    assert.equal(isTransportFailure({ statusCode: 500 }), false);
    assert.equal(isTransportFailure(new Error("Validation failed")), false);
    assert.equal(isTransportFailure(null), false);
});

test("the probe counts any answer but a gateway or server failure", () => {
    assert.equal(healthAnswered(200), true);
    assert.equal(healthAnswered(404), true);
    assert.equal(healthAnswered(502), false);
    assert.equal(healthAnswered(0), false);
});

test("one failed request does not show the strip; a failed probe does", () => {
    const checking = suspect(CONNECTED);
    assert.equal(checking.phase, "checking");
    assert.equal(probeDelay(checking), 0);

    // The server answered the probe: that request was the flake, and nothing
    // needs refetching.
    const fine = afterProbe(checking, true);
    assert.deepEqual(fine, { next: CONNECTED, recovered: false });

    const down = afterProbe(checking, false).next;
    assert.equal(down.phase, "down");
    // Already looking: another failed query does not restart the backoff.
    assert.equal(suspect(down), down);
});

test("recovery after the strip was shown refetches, and the backoff is capped", () => {
    let connection = afterProbe(suspect(CONNECTED), false).next;
    const delays = [probeDelay(connection)];
    for (let i = 0; i < 5; i++) {
        connection = afterProbe(connection, false).next;
        delays.push(probeDelay(connection));
    }
    assert.deepEqual(delays, [1_000, 2_000, 4_000, 5_000, 5_000, 5_000]);
    assert.deepEqual(afterProbe(connection, true), { next: CONNECTED, recovered: true });
});
