import test from "node:test";
import assert from "node:assert/strict";
import type { AgentSurfaceResponse } from "lemma-sdk";
import { surfaceDraft, surfacePatch, surfaceStatus } from "../src/data/surface-settings.ts";
import { readConnectable } from "../src/data/connectable.ts";
import { fields, payload, problems } from "../src/connect/schema.ts";

const surface = {
    id: "surface-example", name: "slack", pod_id: "pod-example", platform: "SLACK", agent_name: "reviewer",
    status: "NEEDS_SETUP", config: {
        channels: [{ channel_id: "C1", channel_name: "launch" }, { channel_id: "C2", channel_name: "archived" }],
        dm_conversation_reset_after_hours: 72, slack: { app_name: "existing-app" }, send_policy: { allow_send: true },
    },
} as AgentSurfaceResponse;

test("editing a responder preserves channel routes, send policy and unowned provider configuration", () => {
    const draft = surfaceDraft(surface);
    assert.equal(draft.enabled, true, "unfinished is not disabled");
    draft.agent = "another-agent";
    const patch = surfacePatch("SLACK", draft);
    assert.equal(patch.default_agent_name, "another-agent");
    assert.equal(Object.hasOwn(patch, "is_enabled"), false, "saving config must not promote NEEDS_SETUP to ACTIVE");
    assert.deepEqual(patch.config?.channels, surface.config.channels);
    assert.deepEqual(patch.config?.send_policy, { allow_send: true });
    assert.equal(Object.hasOwn(patch.config!, "slack"), false);
    assert.equal(Object.hasOwn(patch.config!, "dm_conversation_reset_after_hours"), false);
    assert.equal(Object.hasOwn(patch.config!, "identity"), false);
});

test("email restrictions can be edited and explicitly cleared without adding chat routing settings", () => {
    const draft = surfaceDraft(surface);
    draft.domains = " example.com, example.org\nexample.com ";
    draft.emails = " person@example.com\n other@example.org ";
    const patch = surfacePatch("RESEND", draft);
    assert.deepEqual(patch.config?.identity, { allowed_domains: ["example.com", "example.org"], allowed_email_addresses: ["person@example.com", "other@example.org"] });
    assert.equal(Object.hasOwn(patch.config!, "channels"), false);
    draft.domains = ""; draft.emails = "";
    assert.deepEqual(surfacePatch("RESEND", draft).config?.identity, { allowed_domains: [], allowed_email_addresses: [] });
});

test("disabled connections remain disabled when saving unrelated settings", () => {
    const draft = surfaceDraft({ ...surface, status: "INACTIVE" as AgentSurfaceResponse["status"] });
    draft.agent = "pod_default";
    const patch = surfacePatch("TEAMS", draft);
    assert.equal(Object.hasOwn(patch, "is_enabled"), false);
    draft.enabled = true;
    assert.equal(surfacePatch("TEAMS", draft).is_enabled, true);
    assert.equal(patch.default_agent_name, null);
    draft.channels = [];
    assert.deepEqual(surfacePatch("TEAMS", draft).config?.channels, []);
});

test("setup and disabled states are distinguishable without a contact handle", () => {
    assert.equal(surfaceStatus("PENDING_ADMIN_CONSENT", false), "Needs administrator approval");
    assert.equal(surfaceStatus("NEEDS_SETUP", false), "Finish setup");
    assert.equal(surfaceStatus("INACTIVE", false), "Disabled");
    assert.equal(surfaceStatus("ERROR", false), "Needs attention");
});

test("custom surface credentials retain the catalog kind, validation and secret masking", () => {
    const entry = readConnectable({ platform: "WHATSAPP", connector_id: "whatsapp", kind: "LEMMA", connector_available: true,
        connect: { credential_schema: { type: "object", required: ["access_token", "phone_number_id"], properties: {
            access_token: { type: "string", writeOnly: true }, phone_number_id: { type: "string" }, app_secret: { type: "string" },
        } } } });
    assert.equal(entry?.kind, "LEMMA");
    const list = fields(entry?.credentialSchema);
    assert.equal(list.find(field => field.name === "access_token")?.kind, "secret");
    assert.equal(Object.keys(problems(list, {})).length, 2);
    assert.deepEqual(payload(list, { access_token: "example-token", phone_number_id: "example-number", app_secret: "" }), { access_token: "example-token", phone_number_id: "example-number" });
});

test("header channels belong only to the pod responder, while profiles use exact agent identities", async () => {
    const { surfacesForAgent } = await import("../src/data/surface-settings.ts");
    const base = { id: "pod-email", name: "email", platform: "RESEND", mine: true, agentName: "Lem", agentKey: "pod_default", active: false, handle: "" };
    const entries = [base,
        { ...base, id: "other", mine: false, agentKey: "review_bot", agentName: "Review bot" },
        { ...base, id: "collision", mine: false, agentKey: "review-bot", agentName: "Review bot" },
    ];
    assert.deepEqual(surfacesForAgent(entries).map(row => row.id), ["pod-email"]);
    assert.deepEqual(surfacesForAgent(entries, "review_bot").map(row => row.id), ["other"]);
    assert.deepEqual(surfacesForAgent(entries, "review-bot").map(row => row.id), ["collision"]);
});
