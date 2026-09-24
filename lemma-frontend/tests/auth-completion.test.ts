import test from "node:test";
import assert from "node:assert/strict";
import { accountAccess, needsVerification } from "../src/auth/completion.ts";
import { startVerification, type VerificationActions } from "../src/auth/verification.ts";

test("account access distinguishes verification from sign-out and unrelated denials", async () => {
    process.env.NEXT_PUBLIC_API_URL = "https://api.example.test";
    const probe = (status: number, body: unknown = {}) => accountAccess(async (_url, options) => {
        assert.equal(options?.credentials, "include");
        return new Response(JSON.stringify(body), { status });
    });
    assert.equal(await probe(200), "ready");
    assert.equal(await probe(401), "signed-out");
    assert.equal(await probe(403, { claimValidationErrors: [{ id: "st-ev" }] }), "verify");
    assert.equal(await probe(403, { detail: { code: "EMAIL_VERIFICATION_REQUIRED" } }), "verify");
    await assert.rejects(probe(403, { detail: { code: "ACCOUNT_INACTIVE" } }), /cannot access/);
    await assert.rejects(probe(503), /Try again/);
    assert.equal(needsVerification({ claimValidationErrors: [{ id: "other" }] }), false);
    assert.equal(needsVerification(null), false);
});

function actions(overrides: Partial<VerificationActions> = {}): VerificationActions {
    return {
        access: async () => "verify",
        verify: async () => ({ status: "OK" }),
        send: async () => ({ status: "OK" }),
        refresh: async () => true,
        ...overrides,
    };
}

test("an unverified new account sends an email and waits for verification", async () => {
    let sent = 0;
    const ports = actions({ send: async () => { sent++; return { status: "OK" }; } });
    assert.equal(await startVerification(false, ports), "inbox");
    assert.equal(sent, 1);
    assert.equal(await startVerification(false, ports, true), "inbox");
    assert.equal(sent, 1);
});

test("verified and signed-out accounts do not send verification emails", async () => {
    const send: VerificationActions["send"] = async () => { assert.fail("unexpected email"); };
    assert.equal(await startVerification(false, actions({ access: async () => "ready", send })), "done");
    assert.equal(await startVerification(false, actions({ access: async () => "signed-out", send })), "signed-out");
});

test("a successful email link refreshes the session, including a link in a fresh browser", async () => {
    let refreshed = 0;
    assert.equal(await startVerification(true, actions({ refresh: async () => { refreshed++; return false; } })), "done");
    assert.equal(refreshed, 1);
});

test("an expired link does not refresh or claim success", async () => {
    assert.equal(await startVerification(true, actions({
        verify: async () => ({ status: "EMAIL_VERIFICATION_INVALID_TOKEN_ERROR" }),
        refresh: async () => { assert.fail("must not refresh"); },
    })), "expired");
});

test("an already-verified resend refreshes the session instead of claiming an email was sent", async () => {
    let refreshed = 0;
    assert.equal(await startVerification(false, actions({
        send: async () => ({ status: "EMAIL_ALREADY_VERIFIED_ERROR" }),
        refresh: async () => { refreshed++; return true; },
    })), "done");
    assert.equal(refreshed, 1);
});

test("send and refresh failures remain retryable errors", async () => {
    await assert.rejects(startVerification(false, actions({ send: async () => { throw new Error("rate limited"); } })), /rate limited/);
    await assert.rejects(startVerification(true, actions({ refresh: async () => { throw new Error("offline"); } })), /offline/);
});
