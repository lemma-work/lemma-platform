import test from "node:test";
import assert from "node:assert/strict";
import { EmailCodeError, mintEmailNonce, startEmailCode, resendEmailCode, verifyEmailCode } from "../src/auth/email-code.ts";

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
