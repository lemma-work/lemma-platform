import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { handoffCode } from "../src/desktop/auth-handoff.ts";

/** The browser half of a hosted desktop sign-in never completes on its own.
 *
 *  A request id travels in a URL, and anybody can send a URL. A browser that
 *  was already signed in used to complete whatever request it was pointed at,
 *  which handed the sender's app the recipient's session. */

test("both halves derive the same short code from the request", async () => {
    const id = "Zq3k9V_example-request-id-000";
    const code = await handoffCode(id);
    assert.match(code, /^[ACDEFGHJKMNPQRTUVWXY2345679]{4}-[ACDEFGHJKMNPQRTUVWXY2345679]{4}$/);
    assert.equal(await handoffCode(id), code);
    assert.notEqual(await handoffCode(id + "x"), code);
});

test("completing the request waits for the person's click", async () => {
    const source = await readFile(new URL("../src/desktop/sign-in.tsx", import.meta.url), "utf8");
    const start = source.indexOf("export function DesktopReturn()");
    const returnPart = source.slice(start);
    const effect = returnPart.slice(returnPart.indexOf("useEffect("), returnPart.indexOf("const confirm = async"));
    assert.ok(effect.length > 0);
    assert.ok(!effect.includes("completeRequest("), "the effect completes the request without asking");
    const confirm = returnPart.slice(returnPart.indexOf("const confirm = async"), returnPart.indexOf("const decline"));
    assert.ok(confirm.includes("completeRequest(requestId)"));
    assert.ok(returnPart.includes("onClick={() => void confirm()}"));
    // And the app shows the same code the browser asks about.
    const app = source.slice(0, start);
    assert.ok(app.includes("setCode(await handoffCode(handoff.requestId))"));
});
