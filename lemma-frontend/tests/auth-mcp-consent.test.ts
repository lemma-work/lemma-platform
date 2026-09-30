import test from "node:test";
import assert from "node:assert/strict";
import { answerConsentRequest, requestIdFromSearch, safeRedirect } from "../src/auth/mcp-consent.ts";

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
    const next = await answerConsentRequest("consent-request-id-000123", { allow: true }, fetcher);
    assert.equal(next, "https://claude.ai/api/mcp/auth_callback?code=c&state=s");
    assert.ok(sent);
    assert.equal((sent as { url: string }).url, "https://api.example.test/oauth/consent/consent-request-id-000123");
    assert.deepEqual((sent as { body: unknown }).body, { allow: true, read_only: false });
});

test("a refusal from the API is said in its own words", async () => {
    const fetcher = (async () =>
        new Response(JSON.stringify({ message: "This sign-in request has expired." }), { status: 404 })) as unknown as typeof fetch;
    await assert.rejects(answerConsentRequest("consent-request-id-000123", { allow: true }, fetcher), /has expired/);
});

test("the browser is never sent to a redirect that runs code, whatever the server says", async () => {
    process.env.NEXT_PUBLIC_API_URL = "https://api.example.test";
    for (const bad of [
        "javascript://evil.example/%0aalert(document.domain)//",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "blob:https://evil.example/x",
        "http://evil.example/callback",
        "https://user:pw@claude.ai/cb",
    ]) {
        assert.equal(safeRedirect(bad), false, bad);
        const fetcher = (async () => new Response(JSON.stringify({ redirect_to: bad }), { status: 200 })) as unknown as typeof fetch;
        await assert.rejects(answerConsentRequest("consent-request-id-000123", { allow: false }, fetcher), /unsafe/);
    }
    for (const good of [
        "https://claude.ai/api/mcp/auth_callback?code=c",
        "http://localhost:53172/callback?code=c",
        "cursor://anysphere.cursor-retrieval/oauth/callback?code=c",
    ]) {
        assert.equal(safeRedirect(good), true, good);
    }
});

test("allowing reading only is sent with the answer", async () => {
    process.env.NEXT_PUBLIC_API_URL = "https://api.example.test";
    let body: unknown = null;
    const fetcher = (async (_url: string, init: RequestInit) => {
        body = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ redirect_to: "https://claude.ai/cb?code=c" }), { status: 200 });
    }) as unknown as typeof fetch;
    await answerConsentRequest("consent-request-id-000123", { allow: true, readOnly: true }, fetcher);
    assert.deepEqual(body, { allow: true, read_only: true });
});
