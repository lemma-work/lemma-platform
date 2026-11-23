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
        await liveSource.addProviderKey("org-example", {
            protocol: "google", name: "Example Vertex", baseUrl: "", apiKey: "", models: [],
            google: { backend: "VERTEX_AI", serviceAccountJson: "{\"type\":\"service_account\"}", vertexLocation: "global" },
        });
        await liveSource.addProviderKey("org-example", {
            protocol: "google", name: "Example Gemini", baseUrl: "", apiKey: "test-only", models: ["gemini-example"],
            google: { backend: "GEMINI_API", imageModels: [] },
        });
        assert.deepEqual(sent.map(one => one.path), Array(4).fill("/organizations/org-example/agent-runtime/profiles"));
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
        /* Vertex signs in with the key file and sends no API key; an empty
           model list leaves the choice of Gemini models to the backend. */
        assert.deepEqual(sent[2].body, {
            source: "GOOGLE_GENAI",
            name: "Example Vertex",
            backend: "VERTEX_AI",
            default_model_name: null,
            model_names: [],
            image_model_names: null,
            service_account_json: "{\"type\":\"service_account\"}",
            vertex_project: null,
            vertex_location: "global",
        });
        assert.deepEqual(sent[3].body, {
            source: "GOOGLE_GENAI",
            name: "Example Gemini",
            backend: "GEMINI_API",
            default_model_name: "gemini-example",
            model_names: ["gemini-example"],
            image_model_names: [],
            api_key: "test-only",
        });
    } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
});
