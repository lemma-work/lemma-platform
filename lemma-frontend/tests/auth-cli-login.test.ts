import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { cliRequestFromSearch, deliverToCli, loopbackCallback } from "../src/auth/cli-login.ts";

/** The browser posts a freshly minted CLI session to whatever callback the
 *  link names. Anything but this machine would be handing the tokens away. */

test("only a loopback callback is accepted", () => {
    assert.equal(loopbackCallback("http://127.0.0.1:51210/callback"), "http://127.0.0.1:51210/callback");
    assert.equal(loopbackCallback("http://localhost:4000/callback"), "http://localhost:4000/callback");
    assert.equal(loopbackCallback("http://[::1]:4000/callback"), "http://[::1]:4000/callback");
    assert.equal(loopbackCallback("https://evil.example/callback"), null);
    assert.equal(loopbackCallback("http://127.0.0.1.evil.example/callback"), null);
    assert.equal(loopbackCallback("http://user:pw@127.0.0.1:1/callback"), null);
    assert.equal(loopbackCallback("javascript:alert(1)"), null);
    assert.equal(loopbackCallback("/callback"), null);
    assert.equal(loopbackCallback(null), null);
});

test("the link the CLI opens is read whole, or not at all", () => {
    const search = "?callback=http%3A%2F%2F127.0.0.1%3A51210%2Fcallback&state=udcQhF4b55BkUIKNKeJcSNCgAuwdGkjb";
    assert.deepEqual(cliRequestFromSearch(search), {
        callback: "http://127.0.0.1:51210/callback",
        state: "udcQhF4b55BkUIKNKeJcSNCgAuwdGkjb",
    });
    assert.equal(cliRequestFromSearch("?callback=http%3A%2F%2F127.0.0.1%3A1%2Fcallback"), null);
    assert.equal(cliRequestFromSearch("?callback=https%3A%2F%2Fevil.example%2F&state=x"), null);
});

test("the CLI gets its state back beside the session", async () => {
    let sent: { url: string; body: unknown } | null = null;
    const fetcher = (async (url: string, init: RequestInit) => {
        sent = { url, body: JSON.parse(String(init.body)) };
        return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    await deliverToCli({ callback: "http://127.0.0.1:1/callback", state: "s" }, { access_token: "a" }, fetcher);
    assert.deepEqual(sent, { url: "http://127.0.0.1:1/callback", body: { state: "s", session: { access_token: "a" } } });
});

test("no session is minted until the person confirms", async () => {
    const source = await readFile(new URL("../src/auth/cli-login-screen.tsx", import.meta.url), "utf8");
    const effect = source.slice(source.indexOf("useEffect("), source.indexOf("const confirm = async"));
    assert.ok(effect.length > 0);
    assert.ok(!effect.includes("mintCliSession("), "the effect mints a session without asking");
    assert.ok(source.includes("onClick={() => void confirm()}"));
});
