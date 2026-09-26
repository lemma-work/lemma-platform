import test from "node:test";
import assert from "node:assert/strict";
import { readSnapshot, type ThisMacSnapshot } from "../src/desktop/this-mac.ts";
import {
    AI_PRESETS, CAPABILITIES, aiDraftFrom, aiDraftProblem, aiSectionPayload, capabilityStatus, checklistDismissed,
    dismissChecklist, emailDraftFrom, emailDraftProblem, emailPayloads, needsKey, needsSetup, presetFor, probeKey,
    showChecklist, suggestModels, testFailure,
} from "../src/desktop/server-setup.ts";
import { sendTestEmail } from "../src/desktop/server-setup-email.ts";

/** A snapshot as the shell sends it, with whatever a test overrides. */
function snapshot({ ai = {}, email = {}, secrets = {}, readiness = {} }: {
    ai?: Record<string, unknown>; email?: Record<string, unknown>;
    secrets?: Record<string, boolean>; readiness?: Record<string, string>;
} = {}): ThisMacSnapshot {
    return readSnapshot({
        state: { ready: true, running: true, url: "http://app.lemma.localhost:1/", api_url: "http://app.lemma.localhost:2/" },
        operator: {
            config: {
                revision: 7,
                ai: { protocol: "unconfigured", base_url: "", default_model: "", models: [], vision_models: [], ...ai },
                integrations: { composio_enabled: false, google_client_id: "", microsoft_client_id: "", github_client_id: "", slack_client_id: "" },
                surfaces: { slack_socket_mode: false, telegram_polling: false, teams_app_id: "", teams_tenant_id: "",
                    whatsapp_phone_number_id: "", whatsapp_waba_id: "", resend_inbound_domain: "" },
                email: { provider: "none", from_email: "", smtp_host: "", smtp_port: 587, smtp_user: "", smtp_use_tls: true, ...email },
            },
            secrets,
            readiness: { ai: "needs_setup", ...readiness },
        },
    });
}

/* ── statuses ──────────────────────────────────────────────────────── */

test("a fresh install needs an AI model and nothing else", () => {
    const fresh = snapshot();
    assert.deepEqual(needsSetup(fresh), ["ai"]);
    assert.equal(capabilityStatus(fresh, "ai").label, "Needs setup");
    for (const id of ["email", "connectors", "channels", "voice"] as const) {
        assert.equal(capabilityStatus(fresh, id).state, "optional", id);
    }
    // Search works with no key at all.
    assert.deepEqual(capabilityStatus(fresh, "search"), { state: "ready", label: "Ready · DuckDuckGo" });
    assert.equal(CAPABILITIES.filter((one) => one.required).length, 1);
});

test("statuses follow what is actually stored, not what a field holds", () => {
    const set = snapshot({
        readiness: { ai: "ready" },
        secrets: {
            "surfaces.telegram_bot_token": true, "integrations.deepgram_api_key": true,
            "integrations.brave_search_api_key": true, "integrations.composio_api_key": true,
        },
    });
    assert.deepEqual(needsSetup(set), []);
    assert.equal(capabilityStatus(set, "channels").label, "Ready · Telegram");
    assert.equal(capabilityStatus(set, "connectors").label, "Ready · Composio");
    assert.equal(capabilityStatus(set, "voice").state, "ready");
    assert.equal(capabilityStatus(set, "search").label, "Ready · Brave Search");
    assert.deepEqual(needsSetup(null), []);
});

test("email is ready only with a sender and the credential its provider needs", () => {
    const resend = { provider: "resend", from_email: "a@example.com" };
    assert.equal(capabilityStatus(snapshot({ email: resend }), "email").state, "optional");
    assert.equal(capabilityStatus(snapshot({ email: resend, secrets: { "surfaces.resend_api_key": true } }), "email").state, "ready");
    const smtp = { provider: "smtp", from_email: "a@example.com", smtp_host: "smtp.example.com", smtp_user: "a" };
    assert.equal(capabilityStatus(snapshot({ email: smtp }), "email").state, "optional");
    assert.equal(capabilityStatus(snapshot({ email: smtp, secrets: { "email.smtp_password": true } }), "email").state, "ready");
});

/* ── the AI model ──────────────────────────────────────────────────── */

test("a server on this computer needs no key, and a preset is matched by its address", () => {
    assert.equal(needsKey("http://127.0.0.1:11434/v1"), false);
    assert.equal(needsKey("https://api.openai.com/v1"), true);
    assert.equal(needsKey("http://192.168.1.4:1234/v1"), true);
    assert.equal(presetFor({ protocol: "openai_compat", base_url: "https://api.openai.com/v1/" })?.id, "openai");
    assert.equal(presetFor({ protocol: "anthropic_compat", base_url: "https://api.anthropic.com/v1" })?.id, "anthropic");
    assert.equal(presetFor({ protocol: "openai_compat", base_url: "https://llm.example.com/v1" })?.id, "other");
    // Nothing chosen yet on a fresh install, until "Other" is picked.
    assert.equal(presetFor({ protocol: "openai_compat", base_url: "" }), null);
    assert.equal(presetFor({ protocol: "openai_compat", base_url: "" }, true)?.id, "other");
    // Every preset with an address uses HTTPS, or loopback for a local server.
    for (const preset of AI_PRESETS.filter((one) => one.baseUrl)) {
        assert.ok(preset.baseUrl.startsWith("https://") || !needsKey(preset.baseUrl), preset.id);
    }
});

test("the stored key is used by sending none, a local server gets an empty one, a typed one is sent", () => {
    const draft = aiDraftFrom(snapshot().operator.config.ai);
    assert.deepEqual(probeKey({ ...draft, baseUrl: "https://api.openai.com/v1" }, undefined), {});
    assert.deepEqual(probeKey({ ...draft, baseUrl: "http://127.0.0.1:1234/v1" }, undefined), { api_key: "" });
    assert.deepEqual(probeKey(draft, { action: "replace", value: " sk-1 " }), { api_key: "sk-1" });
});

test("the AI draft says what is missing before it can be saved", () => {
    const draft = { ...aiDraftFrom(snapshot().operator.config.ai), baseUrl: "https://api.openai.com/v1" };
    assert.match(aiDraftProblem(draft, undefined, false)!, /API key/);
    assert.match(aiDraftProblem(draft, undefined, true)!, /List the provider/);
    const listed = suggestModels(draft, ["gpt-a", "gpt-b"]);
    assert.equal(listed.defaultModel, "gpt-a");
    assert.equal(aiDraftProblem(listed, undefined, true), null);
    assert.match(aiDraftProblem({ ...listed, baseUrl: "ftp://x" }, undefined, true)!, /https/);
});

test("a fresh listing keeps choices that are still listed and drops the rest", () => {
    const draft = { ...aiDraftFrom(snapshot().operator.config.ai), defaultModel: "b", imageModel: "gone", fastModel: "c" };
    const next = suggestModels(draft, ["a", "b", "c"]);
    assert.deepEqual([next.defaultModel, next.imageModel, next.fastModel], ["b", "", "c"]);
    // Nothing is guessed from a model's name.
    assert.equal(suggestModels(aiDraftFrom(snapshot().operator.config.ai), ["vision-large", "mini"]).imageModel, "");
});

test("the AI save carries the whole profile, and the key only when one was typed or removed", () => {
    const base = snapshot({ ai: { protocol: "openai_compat", base_url: "https://api.openai.com/v1", default_model: "a", models: ["a", "b"],
        vision_models: ["b"], image_model: "b", fast_model: "", last_validated_at_unix_ms: 5 } });
    const draft = { ...aiDraftFrom(base.operator.config.ai), fastModel: "b" };
    const payload = aiSectionPayload(base, draft, undefined);
    assert.equal(payload.section.name, "ai");
    assert.equal(payload.expected_revision, 7);
    assert.deepEqual(payload.secrets, {});
    const value = payload.section.value as Record<string, unknown>;
    assert.deepEqual(Object.keys(value).sort(), [
        "allow_private_network", "base_url", "default_model", "fast_model", "image_model",
        "last_validated_at_unix_ms", "models", "protocol", "vision_models",
    ]);
    assert.equal(value.fast_model, "b");
    assert.equal(value.last_validated_at_unix_ms, 5);
    assert.deepEqual(aiSectionPayload(base, draft, { action: "replace", value: "sk" }).secrets, { "ai.api_key": { action: "replace", value: "sk" } });
    // A model no longer listed is not sent: the daemon would refuse the save.
    assert.equal((aiSectionPayload(base, { ...draft, fastModel: "gone" }, undefined).section.value as Record<string, unknown>).fast_model, "");
});

/* ── email ─────────────────────────────────────────────────────────── */

test("a Resend key is saved with the channels before the email section that needs it", () => {
    const base = snapshot();
    const draft = { ...emailDraftFrom(base.operator.config.email), provider: "resend" as const, fromEmail: " lemma@example.com " };
    const secrets = { "surfaces.resend_api_key": { action: "replace" as const, value: "re_1" } };
    assert.equal(emailDraftProblem(base, draft, secrets), null);
    const payloads = emailPayloads(base, draft, secrets);
    assert.deepEqual(payloads.map((one) => one.section.name), ["surfaces", "email"]);
    assert.deepEqual(payloads[0].secrets, { "surfaces.resend_api_key": { action: "replace", value: "re_1" } });
    assert.equal((payloads[1].section.value as { from_email: string }).from_email, "lemma@example.com");
    assert.deepEqual(payloads[1].secrets, {});
});

test("an unchanged email draft saves nothing, and an incomplete one says why", () => {
    const base = snapshot();
    assert.deepEqual(emailPayloads(base, emailDraftFrom(base.operator.config.email), {}), []);
    const smtp = { ...emailDraftFrom(base.operator.config.email), provider: "smtp" as const, fromEmail: "a@example.com" };
    assert.match(emailDraftProblem(base, smtp, {})!, /host/);
    assert.match(emailDraftProblem(base, { ...smtp, smtpHost: "smtp://x" }, {})!, /host/);
    assert.match(emailDraftProblem(base, { ...smtp, smtpHost: "smtp.example.com", smtpPort: "0" }, {})!, /port/);
    assert.match(emailDraftProblem(base, { ...smtp, smtpHost: "smtp.example.com", smtpUser: "a" }, {})!, /password/);
    assert.equal(emailDraftProblem(base, { ...smtp, smtpHost: "smtp.example.com", smtpUser: "a" },
        { "email.smtp_password": { action: "replace", value: "p" } }), null);
    const [payload] = emailPayloads(base, { ...smtp, smtpHost: "smtp.example.com", smtpUser: "a" },
        { "email.smtp_password": { action: "replace", value: "p" } });
    assert.equal(payload.section.name, "email");
    assert.deepEqual(payload.secrets, { "email.smtp_password": { action: "replace", value: "p" } });
    assert.equal((payload.section.value as { smtp_port: number }).smtp_port, 587);
});

test("the test email reports the backend's own sentence either way", async () => {
    assert.equal(await sendTestEmail(async () => ({ ok: true, message: "Sent to you@example.com." })), "Sent to you@example.com.");
    await assert.rejects(sendTestEmail(async () => ({ ok: false, message: "Resend refused the sender." })), /Resend refused/);
});

/* ── the checklist ─────────────────────────────────────────────────── */

test("the checklist shows once where This Mac is shown, until it is put away", () => {
    const data = snapshot();
    assert.equal(showChecklist({ shown: true, snapshot: data, dismissed: false }), true);
    assert.equal(showChecklist({ shown: false, snapshot: data, dismissed: false }), false);
    assert.equal(showChecklist({ shown: true, snapshot: null, dismissed: false }), false);
    assert.equal(showChecklist({ shown: true, snapshot: data, dismissed: true }), false);

    const kept = new Map<string, string>();
    const storage = { getItem: (key: string) => kept.get(key) ?? null, setItem: (key: string, value: string) => void kept.set(key, value) };
    assert.equal(checklistDismissed(storage), false);
    dismissChecklist(storage);
    assert.equal(checklistDismissed(storage), true);
    // A storage that throws only means the checklist shows once more.
    const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
    assert.equal(checklistDismissed(broken), false);
    assert.doesNotThrow(() => dismissChecklist(broken));
});

test("a failed test reads as a sentence", () => {
    assert.equal(testFailure(new Error("Error: telegram rejected the key.")), "Telegram rejected the key.");
    assert.equal(testFailure(""), "The test didn’t work.");
});

test("the no-model notice waits for both answers, and a provider key anywhere silences it", async () => {
    const { noModelAnywhere } = await import("../src/desktop/server-setup.ts");
    const none = snapshot();
    assert.equal(noModelAnywhere(none, undefined), false, "still reading the organization's list");
    assert.equal(noModelAnywhere(none, []), true);
    assert.equal(noModelAnywhere(none, [{ kind: "agent", archived: false }]), true, "a coding agent is not a model");
    assert.equal(noModelAnywhere(none, [{ kind: "key", archived: true }]), true, "a retired key is not one either");
    assert.equal(noModelAnywhere(none, [{ kind: "key", archived: false }]), false);
    assert.equal(noModelAnywhere(snapshot({ readiness: { ai: "ready" } }), []), false);
});
