import test from "node:test";
import assert from "node:assert/strict";
import { liveSource } from "../src/data/live.ts";
import { disconnect } from "../src/session/client.ts";
import { readConnectable } from "../src/data/connectable.ts";

test("surface management uses named SDK endpoints and keeps credential binding scoped to its install", async context => {
    const original = process.env.NEXT_PUBLIC_API_URL;
    process.env.NEXT_PUBLIC_API_URL = "https://surface-test.example.invalid";
    disconnect();
    const requests: { path: string; method: string; body: Record<string, unknown> | null }[] = [];
    context.mock.method(globalThis, "fetch", async (input: string | URL | Request, init?: RequestInit) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        assert.equal(url.hostname, "surface-test.example.invalid");
        const method = init?.method ?? "GET";
        const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : null;
        requests.push({ path: url.pathname, method, body });
        let result: unknown = {};
        if (url.pathname.endsWith("/auth-configs") && method === "POST") result = { id: "custom-install" };
        else if (url.pathname.endsWith("/auth-configs")) result = { items: [{ id: "install-example", connector_id: "telegram", kind: "LEMMA", status: "ACTIVE", is_default: true }] };
        else if (url.pathname.endsWith("/accounts") && method === "POST") result = { id: "account-example" };
        else if (url.pathname.endsWith("/accounts")) result = { items: [
            { id: "wrong-account", connector_id: "telegram", auth_config_id: "other-install", status: "CONNECTED" },
            { id: "account-example", connector_id: "telegram", auth_config_id: "install-example", status: "CONNECTED" },
        ] };
        else if (url.pathname.endsWith("/setup")) result = { ready: false, actions: [{ key: "consent" }] };
        else if (url.pathname.endsWith("/channels")) result = { channels: [{ id: "channel-example" }] };
        else result = { id: "surface-example", name: "telegram", platform: "TELEGRAM", config: {}, status: "NEEDS_SETUP" };
        return new Response(JSON.stringify(result), { status: 200, headers: { "content-type": "application/json" } });
    });
    try {
        const entry = readConnectable({ platform: "TELEGRAM", connector_id: "telegram", kind: "LEMMA" })!;
        assert.equal(await liveSource.createSurfaceAccount("org-example", entry, { bot_token: "test-only" }), "account-example");
        const created = requests.find(request => request.method === "POST");
        assert.deepEqual(created?.body, { auth_config_id: "install-example", credentials: { bot_token: "test-only" } });
        await liveSource.addCustomApp("org-example", "slack", "Example bot", { client_id: "example", client_secret: "test-only", signing_secret: "test-signing" }, "LEMMA");
        const install = requests.find(request => request.method === "POST" && request.path.endsWith("/auth-configs"));
        assert.equal(install?.body?.config_source, "ORG_CUSTOM");
        assert.equal(install?.body?.kind, "LEMMA");
        assert.deepEqual(install?.body?.config, { client_id: "example", client_secret: "test-only", signing_secret: "test-signing" });
        assert.equal(await liveSource.findAccount("org-example", "telegram", [], "install-example"), "account-example");
        assert.equal(await liveSource.findAccount("org-example", "telegram", [], "unconnected-install"), "");
        await liveSource.getSurface("pod-example", "telegram");
        assert.equal((await liveSource.surfaceSetup("pod-example", "telegram")).ready, false);
        assert.deepEqual((await liveSource.surfaceChannels("pod-example", "telegram")).channels, [{ id: "channel-example" }]);
        await liveSource.updateSurface("pod-example", "telegram", { default_agent_name: "reviewer", config: { send_policy: { allow_send: true } } });
        const patch = requests.find(request => request.method === "PATCH");
        assert.equal(patch?.path, "/pods/pod-example/surfaces/telegram");
        assert.deepEqual(patch?.body, { default_agent_name: "reviewer", config: { send_policy: { allow_send: true } } });
    } finally {
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
        disconnect();
    }
});
