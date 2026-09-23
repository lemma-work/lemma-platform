import test from "node:test";
import assert from "node:assert/strict";
import { asksForDestination, rawDestination, safeDestinationIn, type Where } from "../src/auth/redirects.ts";

/** Where a sign-in may put somebody down.
 *
 *  This is the one piece of the portal that is a security boundary rather than
 *  a screen. An open redirector on a sign-in page is how a phishing link
 *  borrows a session: the victim signs in for real, and the real sign-in hands
 *  them to somebody else afterwards. Everything below is that rule.
 */

const HERE: Where = { origin: "https://app.example.test", appsSuffix: "apps.example.test" };
const ok = (raw: string | null, where: Where = HERE) => safeDestinationIn(raw, where);

test("somewhere on this origin is honoured", () => {
    assert.ok(ok("/t/marketing/conversation"));
    assert.ok(ok("https://app.example.test/t/marketing/library"));
});

test("somewhere else entirely is refused", () => {
    // Null, not the default. The caller says so rather than substituting —
    // silently swapping the destination is the bug this replaces.
    for (const elsewhere of [
        "https://evil.example/steal",
        "//evil.example/steal",
        "https://app.example.test.evil.example/",
        "http://evil.example",
    ]) {
        assert.equal(ok(elsewhere), null, elsewhere + " should be refused");
    }
});

test("a scheme that is not the web is refused", () => {
    // `javascript:` on a destination that gets assigned to `location` is a
    // script this app would be running on its own origin, with a session.
    for (const scheme of ["javascript:alert(1)", "data:text/html,<script>1</script>", "file:///etc/passwd"]) {
        assert.equal(ok(scheme), null, scheme + " should be refused");
    }
});

test("a deployed pod app is first-party, and a lookalike of one is not", () => {
    // An app on the apps domain is where somebody signing in from an app
    // expects to come back to, and is not this origin.
    assert.ok(ok("https://ledger.apps.example.test/"));
    assert.equal(ok("https://apps.example.test.evil.example/"), null);
    // With no suffix configured, only this origin is first-party.
    assert.equal(ok("https://ledger.apps.example.test/", { ...HERE, appsSuffix: "" }), null);
});

test("the portal refuses to send anybody back into itself", () => {
    // A redirect into sign-in makes a loop that reads as a broken password.
    assert.equal(ok("/auth"), null);
    assert.equal(ok("https://app.example.test/auth"), null);
});

test("nothing asked for is nothing honoured", () => {
    assert.equal(ok(null), null);
    assert.equal(ok(""), null);
});

test("with no origin to judge against, nothing is honoured", () => {
    // Server-rendered, or a browser that cannot say where it is. Refusing is
    // the only safe answer: every destination is off-origin when there is no
    // origin to compare to.
    assert.equal(ok("/t", { origin: "", appsSuffix: "" }), null);
});

/** Reading the ask off the URL. */

test("all three spellings of the parameter are read", () => {
    // `redirect_uri` is what this app and the SDK send. The other two are in
    // links already sitting in people's inboxes.
    assert.equal(rawDestination("?redirect_uri=/t/a"), "/t/a");
    assert.equal(rawDestination("?redirectTo=/t/b"), "/t/b");
    assert.equal(rawDestination("?redirectBack=/t/c"), "/t/c");
    assert.equal(rawDestination("?other=1"), null);
    assert.equal(rawDestination(""), null);
});

test("asking for somewhere is a different question from being allowed it", () => {
    // A link with no destination is somebody who typed the address, and gets
    // the default with nothing said. A link with a refused one is somebody who
    // was sent, and has to be told they are not being sent on.
    assert.equal(asksForDestination("?redirect_uri=https://evil.example"), true);
    assert.equal(asksForDestination(""), false);
    assert.equal(ok(rawDestination("?redirect_uri=https://evil.example")), null);
});
