import test from "node:test";
import assert from "node:assert/strict";
import { siteRuntimeScript, startedWith } from "../src/site/runtime.ts";
import { asksForSignUp } from "../src/auth/config.ts";

/** The desktop app ships one build to every machine and tells it where the API
 *  is only when it starts it -- and again, with every URL changed, when
 *  sharing is turned on. None of that works unless what the server was
 *  started with beats what the build was given. */

test("the environment a server starts with wins over the build's", () => {
    const runtime = startedWith({
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:4100",
        NEXT_PUBLIC_AUTH_URL: "http://127.0.0.1:4200/auth",
        NEXT_PUBLIC_SITE_URL: "http://127.0.0.1:4200",
        NEXT_PUBLIC_LEMMA_DEPLOYMENT: "local",
        NEXT_PUBLIC_LEMMA_RUNTIME_INSTANCE_ID: "run-7",
    });
    assert.equal(runtime.apiUrl, "http://127.0.0.1:4100");
    assert.equal(runtime.authUrl, "http://127.0.0.1:4200/auth");
    assert.equal(runtime.siteUrl, "http://127.0.0.1:4200");
    assert.equal(runtime.deployment, "local");
    assert.equal(runtime.runtimeInstanceId, "run-7");
});

test("blank is a value, except where blank means the default", () => {
    // locald sets the token domain to "" on purpose: host-only cookies.
    const runtime = startedWith({
        NEXT_PUBLIC_SESSION_TOKEN_DOMAIN: "",
        NEXT_PUBLIC_DESKTOP_DOWNLOAD_URL: "",
        NEXT_PUBLIC_LEMMA_DEPLOYMENT: "",
        NEXT_PUBLIC_ANALYTICS_HOST: "",
    });
    assert.equal(runtime.sessionTokenDomain, "");
    // "" is "no download button"; only unset falls back to the public one.
    assert.equal(runtime.desktopDownloadUrl, "");
    assert.equal(runtime.deployment, "hosted");
    assert.equal(runtime.analyticsHost, "https://eu.posthog.com");
});

test("an unset download link stays unset, which is not the same as blank", () => {
    assert.equal(startedWith({}).desktopDownloadUrl, null);
});

test("the script cannot close the tag it is served into", () => {
    const script = siteRuntimeScript({ ...startedWith({}), apiUrl: "</script><script>alert(1)" });
    assert.ok(!script.includes("</script>"));
    assert.ok(script.startsWith("window.__LEMMA_SITE__="));
});

test("show=signup opens sign-up, from the query or the hash", () => {
    assert.equal(asksForSignUp("?show=signup&redirect_uri=/"), true);
    assert.equal(asksForSignUp("", "#show=signup"), true);
    assert.equal(asksForSignUp("?redirect_uri=/"), false);
    assert.equal(asksForSignUp("?show=signin"), false);
});
