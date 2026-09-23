import test from "node:test";
import assert from "node:assert/strict";
import { LemmaClient } from "lemma-sdk";

test("SDK creates the pod default conversation without a named-agent selector", async context => {
    const requests: { url: string; method?: string; body: unknown }[] = [];
    context.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
        requests.push({ url: String(url), method: init.method, body: JSON.parse(String(init.body)) });
        return Response.json({ id: "conversation-1", pod_id: "pod-1", agent_id: null, status: "IDLE" });
    });
    const client = new LemmaClient({ apiUrl: "https://api.example", authUrl: "https://auth.example", podId: "pod-1" });
    const result = await client.conversations.create({ pod_id: "pod-1" });
    assert.equal(result.id, "conversation-1");
    assert.deepEqual(requests, [{ url: "https://api.example/pods/pod-1/conversations", method: "POST", body: {} }]);
});
