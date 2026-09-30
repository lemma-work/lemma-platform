import test from "node:test";
import assert from "node:assert/strict";
import { answerConsentRequest, requestIdFromSearch } from "../src/auth/mcp-consent.ts";

/** The consent page carries nothing but which request it answers; the API
 *  holds the rest and says where the browser goes next. */

test("only a request id is read from the link", () => {
    assert.equal(requestIdFromSearch("?request=consent-request-id-000123"), "consent-request-id-000123");
    assert.equal(requestIdFromSearch("?request=short"), null);
    assert.equal(requestIdFromSearch("?request=has%20spaces%20in%20it%20too%20long"), null);
    assert.equal(requestIdFromSearch("?request=https%3A%2F%2Fevil.example%2Fcallback"), null);
    assert.equal(requestIdFromSearch(""), null);
});

test("the answer goes to the API and the browser goes where the API says", async () => {
    process.env.NEXT_PUBLIC_API_URL = "https://api.example.test";
    let sent: { url: string; body: unknown } | null = null;
    const fetcher = (async (url: string, init: RequestInit) => {
        sent = { url, body: JSON.parse(String(init.body)) };
        return new Response(JSON.stringify({ redirect_to: "https://claude.ai/api/mcp/auth_callback?code=c&state=s" }), {
            status: 200,
        });
    }) as unknown as typeof fetch;
    const next = await answerConsentRequest("consent-request-id-000123", true, fetcher);
    assert.equal(next, "https://claude.ai/api/mcp/auth_callback?code=c&state=s");
    assert.ok(sent);
    assert.equal((sent as { url: string }).url, "https://api.example.test/oauth/consent/consent-request-id-000123");
    assert.deepEqual((sent as { body: unknown }).body, { allow: true });
});

test("a refusal from the API is said in its own words", async () => {
    const fetcher = (async () =>
        new Response(JSON.stringify({ message: "This sign-in request has expired." }), { status: 404 })) as unknown as typeof fetch;
    await assert.rejects(answerConsentRequest("consent-request-id-000123", true, fetcher), /has expired/);
});
