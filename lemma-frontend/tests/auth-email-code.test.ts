import test from "node:test";
import assert from "node:assert/strict";
import { EmailCodeError, continueEmail, mintEmailNonce, startEmailCode, resendEmailCode, verifyEmailCode } from "../src/auth/email-code.ts";

process.env.NEXT_PUBLIC_API_URL = "https://api.example.test";

test("email-code requests keep cookie mode and the same binding through start, resend, and verify", async () => {
    const calls: { path: string; body: unknown }[] = [];
    const fetcher: typeof fetch = async (url, options) => {
        assert.equal(options?.credentials, "include");
        assert.equal(new Headers(options?.headers).get("st-auth-mode"), "cookie");
        assert.equal(options?.method, "POST");
        const path = String(url).split("/").at(-1) ?? "";
        calls.push({ path, body: JSON.parse(String(options?.body)) });
        return Response.json(path === "browser" ? { nonce: "binding" } : path === "verify" ? { status: "complete" }
            : { challenge_id: path, expires_at: new Date(Date.now() + 600_000).toISOString() });
    };
    const nonce = await mintEmailNonce(fetcher);
    const challenge = await startEmailCode(" person@example.test ", nonce, fetcher);
    const replacement = await resendEmailCode(challenge.challenge_id, nonce, fetcher);
    await verifyEmailCode(replacement.challenge_id, nonce, " 123456 ", fetcher);
    assert.deepEqual(calls, [
        { path: "browser", body: {} },
        { path: "start", body: { email: "person@example.test", nonce: "binding" } },
        { path: "resend", body: { challenge_id: "start", nonce: "binding" } },
        { path: "verify", body: { challenge_id: "resend", nonce: "binding", code: "123456" } },
    ]);
});

test("invalid code input does not spend an attempt", async () => {
    for (const value of ["", "12345", "1234567", "abcdef"]) {
        await assert.rejects(verifyEmailCode("challenge", "binding", value, async () => {
            assert.fail("must not send");
        }), /six-digit code/);
    }
});

test("rate limits preserve the server explanation and retry time", async () => {
    await assert.rejects(startEmailCode("person@example.test", "binding", async () => Response.json(
        { message: "Too many code requests" }, { status: 429, headers: { "retry-after": "90" } },
    )), (error: unknown) => {
        assert.ok(error instanceof EmailCodeError);
        assert.equal(error.retryAfter, 90);
        assert.equal(error.status, 429);
        assert.match(error.message, /Too many code requests.*2 minutes/);
        return true;
    });
});

test("invalid, expired, and missing-browser responses remain actionable", async () => {
    for (const message of ["The code did not match; try again", "Code expired or attempts exhausted", "Login expired; start again in this browser"]) {
        await assert.rejects(verifyEmailCode("challenge", "binding", "123456", async () => Response.json(
            { detail: message }, { status: 400 },
        )), { message });
    }
});

test("malformed successful responses never advance the flow", async () => {
    const malformed: typeof fetch = async () => Response.json({});
    await assert.rejects(mintEmailNonce(malformed), /start sign-in/);
    await assert.rejects(startEmailCode("person@example.test", "binding", malformed), /send a code/);
    await assert.rejects(verifyEmailCode("challenge", "binding", "123456", malformed), /confirm your code/);
    await assert.rejects(startEmailCode("person@example.test", "binding", async () => new Response("bad gateway", { status: 502 })), /Unable to continue/);
});

for (const answer of [{ method: "password" }, { method: "thirdparty", provider: "google" }, { method: "thirdparty", provider: "active-directory" }, { method: "code", challenge_id: "new", expires_at: "2030-01-01T00:00:00Z" }]) {
    test(`continue selects ${JSON.stringify(answer)} and abandons the previous challenge`, async () => {
        const result = await continueEmail(" person@example.test ", "binding", "old", async (url, options) => {
            assert.match(String(url), /email-code\/continue$/);
            assert.deepEqual(JSON.parse(String(options?.body)), { email: "person@example.test", nonce: "binding", abandon_challenge_id: "old" });
            return Response.json(answer);
        });
        assert.deepEqual(result, answer);
    });
}

test("coded proof rejection retries once and preserves the error code", async () => {
    let attempts = 0;
    await assert.rejects(continueEmail("person@example.test", "binding", null, async url => {
        if (String(url).includes("altcha/challenge")) return Response.json({ enabled: false });
        attempts++;
        return Response.json({ code: "EMAIL_LOGIN_PROOF_REJECTED", message: "Proof required" }, { status: 403 });
    }), (error: unknown) => {
        assert.ok(error instanceof EmailCodeError);
        assert.equal(error.code, "EMAIL_LOGIN_PROOF_REJECTED");
        return true;
    });
    assert.equal(attempts, 2);
});

test("unknown methods fail instead of silently starting email signup", async () => {
    await assert.rejects(continueEmail("person@example.test", "binding", null, async () => Response.json({ method: "thirdparty", provider: "unknown" })), /determine how/);
});

test("proof escalation solves the challenge and retries the same lookup", async () => {
    const salt = "test-salt", signature = "test-signature";
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(salt + "0"));
    const challenge = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    let lookups = 0;
    const answer = await continueEmail("person@example.test", "binding", null, async (url, options) => {
        if (String(url).includes("altcha/challenge")) {
            assert.match(String(url), /purpose=signin-risk/);
            return Response.json({ enabled: true, algorithm: "SHA-256", salt, signature, challenge, maxnumber: 0 });
        }
        lookups++;
        if (lookups === 1) return Response.json({ code: "EMAIL_LOGIN_PROOF_REJECTED", message: "Proof required" }, { status: 400 });
        const proof = new Headers(options?.headers).get("x-altcha-payload");
        assert.ok(proof);
        assert.equal(JSON.parse(atob(proof.replace(/-/g, "+").replace(/_/g, "/"))).number, 0);
        return Response.json({ method: "password" });
    });
    assert.deepEqual(answer, { method: "password" });
    assert.equal(lookups, 2);
});
