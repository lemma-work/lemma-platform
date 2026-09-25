import test from "node:test";
import assert from "node:assert/strict";
import { asksForDestination, rawDestination, safeDestinationIn, authLink, landing, rememberDestination, storedDestination, type Where } from "../src/auth/redirects.ts";

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
    for (const path of ["/auth/signup", "/login", "/signup", "/verify-email", "/reset-password"]) {
        assert.equal(ok(path), null);
    }
});

test("the configured API is trusted for admin and connector returns", () => {
    const where = { ...HERE, apiOrigin: "https://api.example.test/gateway/" };
    assert.equal(ok("https://api.example.test/admin", where), "https://api.example.test/admin");
    assert.equal(ok("https://api.example.test.evil.example/admin", where), null);
    assert.equal(ok("https://other.example.test/admin", where), null);
});

test("auth steps preserve the destination without carrying sensitive link parameters", (t) => {
    const values = new Map<string, string>();
    const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
    t.after(() => {
        if (previous) Object.defineProperty(globalThis, "window", previous);
        else Reflect.deleteProperty(globalThis, "window");
    });
    Object.defineProperty(globalThis, "window", { configurable: true, value: {
        location: { origin: HERE.origin, search: "" },
        sessionStorage: {
            getItem: (key: string) => values.get(key) ?? null,
            setItem: (key: string, value: string) => { values.set(key, value); },
            removeItem: (key: string) => { values.delete(key); },
        },
    } });
    const target = HERE.origin + "/t/example?tab=files#section";
    const search = "?redirect_uri=" + encodeURIComponent(target) + "&token=secret&code=secret";
    const signup = authLink("/auth/signup", search);
    assert.equal(new URL(signup, HERE.origin).searchParams.get("redirect_uri"), target);
    assert.equal(new URL(signup, HERE.origin).searchParams.has("token"), false);
    assert.equal(new URL(signup, HERE.origin).searchParams.has("code"), false);
    rememberDestination(target);
    assert.equal(new URL(authLink("/auth", "?token=reset"), HERE.origin).searchParams.get("redirect_uri"), target);
    assert.equal(landing(""), target);
    assert.equal(storedDestination(), null);
    rememberDestination(target);
    assert.equal(landing(search), target);
    assert.equal(storedDestination(), null);
    rememberDestination(target);
    assert.equal(landing("?redirect_uri=https://evil.example"), "/t");
    assert.equal(storedDestination(), null);
    rememberDestination(target);
    rememberDestination(null);
    assert.equal(landing(""), "/t");
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

test("a sign-up headed for an invitation carries that invitation", async () => {
    const { invitationIn } = await import("../src/auth/redirects.ts");
    const id = "6f1d8c1e-2c2a-4b0e-9d3b-0c9a0f5e7a11";
    assert.equal(invitationIn("/invitations/" + id + "/accept"), id);
    assert.equal(invitationIn("http://192.168.1.20:61234/invitations/" + id + "/accept"), id);
    assert.equal(invitationIn("/invitations/" + id + "/reject"), null);
    assert.equal(invitationIn("/invitations/not-an-id/accept"), null);
    assert.equal(invitationIn("/t"), null);
    assert.equal(invitationIn(null), null);
});
