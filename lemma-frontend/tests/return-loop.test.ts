import test from "node:test";
import assert from "node:assert/strict";
import {
    RETURNS_BEFORE_LOOP,
    WINDOW_MS,
    appOrigin,
    isLoop,
    parseReturns,
    withReturn,
    withoutOrigin,
    type Returns,
} from "../src/auth/return-loop.ts";

/** When the portal stops handing a signed-in person back to an app.
 *
 *  Every request in a sign-in loop succeeds, so nothing else notices it: the
 *  portal is the one place that sees the same person sent to the same app over
 *  and over. Stop too late and they watch two pages flicker; stop too early and
 *  an ordinary sign-in gets a screen it did not need.
 */

const HERE = "https://app.example.test";
const APP = "https://tracker.apps.example.test";
const NOW = 1_000_000_000;

function returnsAt(...times: number[]): Returns {
    return times.reduce<Returns>((returns, at) => withReturn(returns, APP, at), {});
}

test("an app on another origin is counted, and this site's own pages are not", () => {
    assert.equal(appOrigin(APP + "/?_r=x", HERE), APP);
    assert.equal(appOrigin("/t/marketing", HERE), null);
    assert.equal(appOrigin(HERE + "/t", HERE), null);
    assert.equal(appOrigin("http://[not a url", HERE), null);
});

test("the third return in a minute is the loop, and the first two are not", () => {
    let returns: Returns = {};
    for (let made = 0; made < RETURNS_BEFORE_LOOP; made += 1) {
        assert.equal(isLoop(returns, APP, NOW + made), false, `return ${made + 1} should go through`);
        returns = withReturn(returns, APP, NOW + made);
    }
    assert.equal(isLoop(returns, APP, NOW + RETURNS_BEFORE_LOOP), true);
});

test("returns older than the window do not count", () => {
    const returns = returnsAt(NOW - WINDOW_MS - 1, NOW - 10);
    assert.equal(isLoop(returns, APP, NOW), false);
});

test("returns stamped after now, by a clock since set back, do not count", () => {
    const returns = returnsAt(NOW + 5_000, NOW + 10_000);
    assert.equal(isLoop(returns, APP, NOW), false);
    assert.deepEqual(withReturn(returns, APP, NOW), { [APP]: [NOW] });
});

test("one app looping does not stop a sign-in to another", () => {
    const returns = returnsAt(NOW - 2, NOW - 1);
    assert.equal(isLoop(returns, APP, NOW), true);
    assert.equal(isLoop(returns, "https://other.apps.example.test", NOW), false);
});

test("recording a return drops whatever has left the window", () => {
    const stale = withReturn({}, "https://other.apps.example.test", NOW - WINDOW_MS - 1);
    const next = withReturn(stale, APP, NOW);
    assert.deepEqual(next, { [APP]: [NOW] });
});

test("trying again starts the count over for that app", () => {
    const returns = withoutOrigin(returnsAt(NOW - 2, NOW - 1), APP);
    assert.equal(isLoop(returns, APP, NOW), false);
});

test("a stored value that is not a record of times is treated as nothing", () => {
    // Session storage is the person's to edit. A value that does not parse
    // must never be what stops their sign-in.
    for (const raw of [null, "", "not json", "[]", "42", '"text"', "null"]) {
        assert.deepEqual(parseReturns(raw), {}, String(raw));
    }
    assert.deepEqual(
        parseReturns(JSON.stringify({ [APP]: [1, "2", null, 3], other: "x", empty: [] })),
        { [APP]: [1, 3] },
    );
});
