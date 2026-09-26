import test from "node:test";
import assert from "node:assert/strict";
import { configuredProviders, fetchConfiguredProviders } from "../src/auth/login-methods.ts";
import { isExistingAccount } from "../src/auth/errors.ts";

process.env.NEXT_PUBLIC_API_URL = "https://api.example.test";

/** SuperTokens' `/loginmethods` answer, as the backend's recipe writes it. */
function answer(providers: { id: string; name: string }[], enabled = true) {
    return {
        status: "OK",
        emailPassword: { enabled: true },
        thirdParty: { enabled, providers },
        passwordless: { enabled: false },
        firstFactors: ["emailpassword", "thirdparty"],
    };
}

test("only the providers the deployment registered get a button", () => {
    assert.deepEqual(configuredProviders(answer([])), []);
    assert.deepEqual(configuredProviders(answer([{ id: "google", name: "Google" }])), ["google"]);
    assert.deepEqual(
        configuredProviders(answer([{ id: "active-directory", name: "Microsoft" }, { id: "google", name: "Google" }])),
        ["google", "active-directory"],
    );
});

test("a provider this app has no button for is not drawn", () => {
    assert.deepEqual(configuredProviders(answer([{ id: "github", name: "GitHub" }])), []);
});

test("a disabled or malformed answer reads as no providers, never as all of them", () => {
    assert.deepEqual(configuredProviders(answer([{ id: "google", name: "Google" }], false)), []);
    assert.deepEqual(configuredProviders(null), []);
    assert.deepEqual(configuredProviders({ status: "GENERAL_ERROR" }), []);
    assert.deepEqual(configuredProviders({ status: "OK", thirdParty: { providers: "google" } }), []);
});

test("the question goes to SuperTokens' own login-methods endpoint", async () => {
    const asked: string[] = [];
    const fetcher: typeof fetch = async (url) => {
        asked.push(String(url));
        return Response.json(answer([{ id: "google", name: "Google" }]));
    };
    assert.deepEqual(await fetchConfiguredProviders(fetcher), ["google"]);
    assert.deepEqual(asked, ["https://api.example.test/st/auth/loginmethods"]);
});

test("an unreachable or refusing API draws no provider buttons", async () => {
    const down: typeof fetch = async () => { throw new TypeError("network"); };
    const refusing: typeof fetch = async () => new Response("no", { status: 500 });
    assert.deepEqual(await fetchConfiguredProviders(down), []);
    assert.deepEqual(await fetchConfiguredProviders(refusing), []);
});

test("SuperTokens' existing-account complaint is recognised, other email complaints are not", () => {
    assert.equal(isExistingAccount("This email already exists. Please sign in instead."), true);
    assert.equal(isExistingAccount("Email is invalid"), false);
    assert.equal(isExistingAccount(undefined), false);
});
