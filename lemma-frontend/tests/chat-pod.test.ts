import test from "node:test";
import assert from "node:assert/strict";
import { chatPodLabels, readChatPodChoice } from "../src/data/chat-pod.ts";
import { liveSource } from "../src/data/live.ts";
import { disconnect } from "../src/session/client.ts";

/** Which pod answers the person on Lemma's shared WhatsApp number. */

const pods = [
    { pod_id: "p-trip", name: "Trip", organization_name: "Acme" },
    { pod_id: "p-home", name: "Personal", organization_name: "Acme" },
    { pod_id: "p-north", name: "Personal", organization_name: "Northfield" },
];

test("every pod the person belongs to is offered, surface or not", () => {
    const choice = readChatPodChoice({ groups: [{
        platform: "WHATSAPP",
        surfaces: [{ id: "s-trip", pod_id: "p-trip" }],
        default_surface_id: "s-trip",
        available_pods: pods,
    }] }, "WHATSAPP");
    assert.deepEqual(choice?.options.map((option) => option.podId).sort(), ["p-home", "p-north", "p-trip"]);
    assert.equal(choice?.currentPodId, "p-trip");
});

test("a repeated name carries its organization; a unique one does not", () => {
    assert.deepEqual(
        chatPodLabels(pods.map((pod) => ({ podId: pod.pod_id, name: pod.name, organizationName: pod.organization_name }))).map((option) => option.label),
        ["Trip", "Personal · Acme", "Personal · Northfield"],
    );
});

test("the server's default pod wins over the surface it would infer", () => {
    const choice = readChatPodChoice({ groups: [{
        platform: "WHATSAPP",
        surfaces: [{ id: "s-trip", pod_id: "p-trip" }],
        default_surface_id: "s-trip",
        default_pod_id: "p-north",
        available_pods: pods,
    }] }, "whatsapp");
    assert.equal(choice?.currentPodId, "p-north");
});

test("with several surfaces and no default, nothing is claimed to be answering", () => {
    const choice = readChatPodChoice({ groups: [{
        platform: "WHATSAPP",
        surfaces: [{ id: "a", pod_id: "p-trip" }, { id: "b", pod_id: "p-home" }],
        available_pods: pods,
    }] }, "WHATSAPP");
    assert.equal(choice?.currentPodId, null);
});

test("nothing to choose hides the control", () => {
    assert.equal(readChatPodChoice({ groups: [] }, "WHATSAPP"), null);
    assert.equal(readChatPodChoice({ groups: [{ platform: "WHATSAPP", surfaces: [] }] }, "WHATSAPP"), null);
    assert.equal(readChatPodChoice({ groups: [{ platform: "WHATSAPP", available_pods: [] }] }, "WHATSAPP"), null);
    assert.equal(readChatPodChoice({ groups: [{ platform: "SLACK", available_pods: pods }] }, "WHATSAPP"), null);
    assert.equal(readChatPodChoice(null, "WHATSAPP"), null);
});

test("picking a pod sends its id, and a refusal arrives as the server's sentence", async (context) => {
    const original = process.env.NEXT_PUBLIC_API_URL;
    process.env.NEXT_PUBLIC_API_URL = "https://chat-pod-test.example.invalid";
    disconnect();
    const sent: { method: string; path: string; body: unknown }[] = [];
    let refuse = false;
    context.mock.method(globalThis, "fetch", async (input: string | URL | Request, init?: RequestInit) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const method = init?.method ?? "GET";
        sent.push({ method, path: url.pathname, body: typeof init?.body === "string" ? JSON.parse(init.body) : null });
        if (refuse) {
            return new Response(JSON.stringify({ message: "Trip already answers WhatsApp on its own number." }), {
                status: 409, headers: { "content-type": "application/json" },
            });
        }
        return new Response(JSON.stringify({ groups: [] }), { status: 200, headers: { "content-type": "application/json" } });
    });
    try {
        await liveSource.setChatPod("WHATSAPP", "p-home");
        const put = sent.find((request) => request.method === "PUT");
        assert.ok(put?.path.endsWith("/surfaces/me/default"));
        assert.deepEqual(put?.body, { platform: "WHATSAPP", pod_id: "p-home" });

        refuse = true;
        await assert.rejects(liveSource.setChatPod("WHATSAPP", "p-trip"), /already answers WhatsApp/);
    } finally {
        disconnect();
        if (original === undefined) delete process.env.NEXT_PUBLIC_API_URL;
        else process.env.NEXT_PUBLIC_API_URL = original;
    }
});
