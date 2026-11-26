import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
    dropRequestId,
    handoffAppFromSearch,
    handoffCode,
    handoffWords,
    heldHandoffApp,
    heldRequestId,
    holdRequestId,
} from "../src/desktop/auth-handoff.ts";

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

/** The phone app signs in through this same handoff and says so with
 *  `client=mobile`; only the page's words change. */

function withSessionStorage(run: () => void): void {
    const held = new Map<string, string>();
    const storage = {
        getItem: (key: string) => held.get(key) ?? null,
        setItem: (key: string, value: string) => void held.set(key, value),
        removeItem: (key: string) => void held.delete(key),
    };
    const global = globalThis as { window?: unknown };
    const before = global.window;
    global.window = { sessionStorage: storage };
    try {
        run();
    } finally {
        global.window = before;
    }
}

test("only client=mobile marks a request as the phone app's", () => {
    assert.equal(handoffAppFromSearch("?desktop_browser=1&desktop_request=abc&client=mobile"), "mobile");
    assert.equal(handoffAppFromSearch("?desktop_browser=1&desktop_request=abc"), "desktop");
    assert.equal(handoffAppFromSearch("?desktop_request=abc&client=ios"), "desktop");
});

test("which app asked is held with the request and dropped with it", () => {
    const id = "Zq3k9V_example-request-id-000";
    withSessionStorage(() => {
        holdRequestId(id, "mobile");
        assert.equal(heldRequestId(), id);
        assert.equal(heldHandoffApp(), "mobile");
        // A desktop link opened later in the same tab is the desktop app's again.
        holdRequestId(id);
        assert.equal(heldHandoffApp(), "desktop");
        holdRequestId(id, "mobile");
        dropRequestId();
        assert.equal(heldRequestId(), null);
        assert.equal(heldHandoffApp(), "desktop");
    });
});

test("the desktop app's words stay as they were", () => {
    const desktop = handoffWords("desktop");
    assert.equal(desktop.name, "Lemma Desktop");
    assert.equal(desktop.where, "on this computer");
    assert.equal(desktop.back, "Lemma Desktop comes to the front and opens your workspace. You can close this tab.");
    const mobile = handoffWords("mobile");
    assert.equal(mobile.where, "on this phone");
    assert.ok(!Object.values(mobile).some((said) => /Desktop|computer/.test(said)));
});
