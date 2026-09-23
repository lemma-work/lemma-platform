import test from "node:test";
import assert from "node:assert/strict";
import { authFailure, retryAfterSeconds, sayDelay, sayProblem } from "../src/auth/errors.ts";
import { solve, type Challenge } from "../src/auth/altcha.ts";

/** A locked-out person reading the screen. Every branch here is the difference
 *  between somebody waiting the right amount of time and somebody closing the
 *  tab. */

const NOW = Date.parse("2026-09-20T12:00:00Z");

test("`retry-after` is read as seconds or as a date", () => {
    assert.equal(retryAfterSeconds("90", NOW), 90);
    assert.equal(retryAfterSeconds("  45  ", NOW), 45);
    assert.equal(retryAfterSeconds("Sun, 20 Sep 2026 12:02:00 GMT", NOW), 120);
    assert.equal(retryAfterSeconds(null, NOW), null);
    assert.equal(retryAfterSeconds("", NOW), null);
    assert.equal(retryAfterSeconds("soon", NOW), null);
});

test("a date already past is a second, never a negative number", () => {
    // It would otherwise reach the screen as "try again in -3 seconds".
    assert.equal(retryAfterSeconds("Sun, 20 Sep 2026 11:59:57 GMT", NOW), 1);
});

test("a wait is said in units somebody does not have to convert", () => {
    assert.equal(sayDelay(1), "1 second");
    assert.equal(sayDelay(45), "45 seconds");
    assert.equal(sayDelay(60), "1 minute");
    assert.equal(sayDelay(90), "2 minutes");
});

test("a rate limit says which attempt, and how long", () => {
    assert.equal(
        authFailure("sign-in", 429, "90", "", NOW),
        "Too many sign-in attempts. Try again in 2 minutes.",
    );
    assert.equal(
        authFailure("reset", 429, null, "", NOW),
        "Too many password reset requests. Wait a little and try again.",
    );
});

test("the rate limit is recognised however it arrives", () => {
    // It comes back both as a 429 and as a refusal carrying this sentence.
    assert.ok(authFailure("sign-up", 200, null, "Too many authentication attempts", NOW).startsWith("Too many attempts to make an account."));
});

test("an expired security check is not blamed on what they typed", () => {
    // Retyping a correct password will not fix it, so the sentence must not
    // imply that it might.
    const said = authFailure("sign-in", 403, null, "Missing proof-of-work", NOW);
    assert.equal(said, "The security check expired before that went through. Try again.");
});

test("the server being broken reads differently from the person being wrong", () => {
    assert.ok(authFailure("sign-in", 503, null, "", NOW).includes("temporarily unavailable"));
    assert.equal(authFailure("sign-in", 400, null, "", NOW), "That could not be completed. Try again.");
});

/** The proof-of-work itself. */

async function challengeFor(salt: string, number: number, maxnumber: number): Promise<Challenge> {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(salt + number));
    const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
    return { enabled: true, algorithm: "SHA-256", challenge: hex, salt, signature: "sig", maxnumber };
}

test("the proof-of-work finds the number the server hid", () => {
    return (async () => {
        const proof = await solve(await challengeFor("abc123", 317, 2000));
        assert.ok(proof, "a solvable challenge must produce a proof");
        const decoded = JSON.parse(Buffer.from(proof!.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString());
        assert.equal(decoded.number, 317);
        assert.equal(decoded.salt, "abc123");
        assert.equal(decoded.signature, "sig");
        // Base64url: these do not survive a header value reliably.
        assert.ok(!/[+/=]/.test(proof!));
    })();
});

test("protection that is off asks for nothing", () => {
    return (async () => {
        assert.equal(await solve({ enabled: false }), null);
    })();
});

test("a challenge with no answer under the ceiling is refused, not looped forever", () => {
    return (async () => {
        const real = await challengeFor("abc123", 900, 900);
        await assert.rejects(() => solve({ ...real, maxnumber: 10 }), /could not be completed/);
    })();
});

test("a challenge this app cannot answer says so rather than guessing", () => {
    return (async () => {
        await assert.rejects(() => solve({ enabled: true, algorithm: "SHA-512", challenge: "x", salt: "s", signature: "g", maxnumber: 10 }), /cannot answer/);
        await assert.rejects(() => solve({ enabled: true, algorithm: "SHA-256", salt: "s", signature: "g", maxnumber: 10 }), /cannot answer/);
    })();
});

test("the API's own words about the proof-of-work do not reach the screen", () => {
    // Verified against the live API: the reset endpoint answers 400 with
    // `{"status":"GENERAL_ERROR","message":"Missing proof-of-work"}`, and
    // SuperTokens raises that message as an Error. Printed as-is it names
    // something the reader neither did nor can do.
    assert.equal(sayProblem(new Error("Missing proof-of-work")), "The security check expired before that went through. Try again.");
    assert.equal(sayProblem(new Error("Security check failed")), "The security check expired before that went through. Try again.");
});

test("a server that explained itself is not talked over", () => {
    assert.equal(sayProblem(new Error("That account is not allowed to sign in here.")), "That account is not allowed to sign in here.");
});

test("something thrown that is not an error at all still says something", () => {
    assert.ok(sayProblem(null).includes("could not be reached"));
    assert.ok(sayProblem(new Error("")).includes("could not be reached"));
});
