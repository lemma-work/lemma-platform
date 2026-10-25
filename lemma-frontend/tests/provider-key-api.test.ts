import test from "node:test";
import assert from "node:assert/strict";
import { liveSource } from "../src/data/live.ts";
import { disconnect } from "../src/session/client.ts";

/** "Connect a key" creates a runtime profile, and the backend tells its three
 *  kinds apart by `source` (`CreateAgentRuntimeProfileRequest`, a union on a
 *  discriminator). A body without it is refused before any of its fields are
 *  read, so each protocol has to name its own. */
test("a provider key names the kind of profile it is", async context => {
    const original = process.env.NEXT_PUBLIC_API_URL;
    process.env.NEXT_PUBLIC_API_URL = "https://provider-key-test.example.invalid";
    disconnect();
    const sent: { path: string; body: Record<string, unknown> }[] = [];
    context.mock.method(globalThis, "fetch", async (input: string | URL | Request, init?: RequestInit) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        assert.equal(url.hostname, "provider-key-test.example.invalid");
        assert.equal(init?.method, "POST");
        sent.push({ path: url.pathname, body: JSON.parse(String(init?.body)) as Record<string, unknown> });
        return new Response(JSON.stringify({ id: "profile-example" }), { status: 200, headers: { "content-type": "application/json" } });
    });
    try {
        await liveSource.addProviderKey("org-example", {
            protocol: "openai", name: "Example route", baseUrl: "https://models.example.invalid/v1", apiKey: "test-only", models: ["small", "large"], visionModels: ["large"],
        });
        await liveSource.addProviderKey("org-example", {
            protocol: "anthropic", name: "Example Claude", baseUrl: "", apiKey: "test-only", models: ["claude-example"],
        });
        assert.deepEqual(sent.map(one => one.path), ["/organizations/org-example/agent-runtime/profiles", "/organizations/org-example/agent-runtime/profiles"]);
        assert.deepEqual(sent[0].body, {
            source: "OPENAI_COMPATIBLE",
            name: "Example route",
            api_key: "test-only",
            base_url: "https://models.example.invalid/v1",
            default_model_name: "small",
            model_names: ["small", "large"],
            vision_model_names: ["large"],
        });
        assert.deepEqual(sent[1].body, {
            source: "ANTHROPIC_COMPATIBLE",
            name: "Example Claude",
            api_key: "test-only",
            base_url: null,
            default_model_name: "claude-example",
            model_names: ["claude-example"],
        });
    } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
});
