import test from "node:test";
import assert from "node:assert/strict";
import { redirectUriOf } from "../src/data/oauth-redirect.ts";

test("the redirect URI is the backend's, verbatim", () => {
    const uri = "http://127.0.0.1:8711/connectors/connect-requests/oauth/callback";
    assert.equal(redirectUriOf({ id: "github", oauth_redirect_uri: uri }), uri);
});

test("an older server, or none yet, gives no URI rather than a guessed one", () => {
    assert.equal(redirectUriOf({ id: "github" }), null);
    assert.equal(redirectUriOf({ oauth_redirect_uri: "  " }), null);
    assert.equal(redirectUriOf(undefined), null);
});
