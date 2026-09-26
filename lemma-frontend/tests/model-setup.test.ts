import test from "node:test";
import assert from "node:assert/strict";
import { needsAiModel, noModelSentence, runFailure } from "../src/thread/model-setup.ts";
import {
    chosenVisionModels,
    discoveryRequest,
    isLocalRoute,
    keyToSend,
    localRouteAnswering,
    modelNames,
    readDiscoveredModels,
    visionCandidates,
} from "../src/org/provider-draft.ts";
import { modelSetupState } from "../src/shell/runs-on-state.ts";
import type { Runtime } from "../src/data/runtimes.ts";

/* ── recognising "no AI model is set up" ───────────────────────────── */

test("a refused send, a failed stream and a stored run are all recognised by code", () => {
    const refused = Object.assign(new Error("No AI model is set up yet."), { code: "model_not_configured" });
    assert.equal(needsAiModel(refused), true);
    assert.equal(needsAiModel(null, { last_run_error_code: "model_not_configured" }), true);
    assert.equal(needsAiModel("model_not_configured"), true);
});

test("other failures, and the words without the code, are not dressed with a setup link", () => {
    const archived = Object.assign(new Error("The model was removed."), { code: "runtime_profile_archived" });
    assert.equal(needsAiModel(archived), false);
    assert.equal(needsAiModel(new Error("No AI model is set up yet.")), false);
    assert.equal(needsAiModel(null, undefined, { last_run_error_code: null }), false);
});

/* ── the Connect a key dialog ──────────────────────────────────────── */

test("the Models field becomes names, without blanks or repeats", () => {
    assert.deepEqual(modelNames(" gpt-5, ,o3-mini,gpt-5 "), ["gpt-5", "o3-mini"]);
    assert.deepEqual(modelNames(""), []);
});

test("image ticks are offered on what was typed, else on what a Test found", () => {
    assert.deepEqual(visionCandidates(["typed"], ["found"]), ["typed"]);
    assert.deepEqual(visionCandidates([], ["found"]), ["found"]);
});

test("only ticks on models still on offer are sent, and none for Anthropic", () => {
    assert.deepEqual(chosenVisionModels("openai", ["eyes", "gone"], ["eyes", "words"]), ["eyes"]);
    assert.deepEqual(chosenVisionModels("anthropic", ["eyes"], ["eyes"]), []);
});

test("a Test sends the typed key, even an empty one, so the stored key is never borrowed", () => {
    const request = discoveryRequest("openai", " https://route.test/v1 ", "") as {
        ai: { protocol: string; base_url: string };
        api_key: string;
    };
    assert.equal(request.api_key, "");
    assert.equal(request.ai.base_url, "https://route.test/v1");
    assert.equal(request.ai.protocol, "openai_compat");
    assert.equal((discoveryRequest("anthropic", "x", "k") as { ai: { protocol: string } }).ai.protocol, "anthropic_compat");
});

test("discovered models are read whatever shape the lookup answers in", () => {
    assert.deepEqual(readDiscoveredModels(["a", { id: "b" }, { name: "c" }, "a", 7, null]), ["a", "b", "c"]);
    assert.deepEqual(readDiscoveredModels({ models: ["a"] }), []);
});

/* ── the runs-on picker ────────────────────────────────────────────── */

function runtime(id: string): Runtime {
    return {
        id, name: id, kind: "key", harness: "", harnessId: "", models: [], defaultModel: "",
        selections: {}, scope: "org", archived: false, trouble: "",
    };
}

test("no model anywhere says so, whatever the default claims", () => {
    assert.equal(modelSetupState([], { runtimeId: "system:lemma", model: "" }, null), "none");
});

test("a default naming a provider that is not there is flagged only while it is followed", () => {
    const live = [runtime("org-key")];
    const systemDefault = { runtimeId: "system:lemma", model: "" };
    assert.equal(modelSetupState(live, systemDefault, null), "default-missing");
    assert.equal(modelSetupState(live, systemDefault, { runtimeId: "org-key", model: "" }), null);
    assert.equal(modelSetupState([runtime("system:lemma")], systemDefault, null), null);
});

test("a default still being read, or never stated, is not called missing", () => {
    assert.equal(modelSetupState([runtime("org-key")], undefined, null), null);
    assert.equal(modelSetupState([runtime("org-key")], null, null), null);
});

test("a reopened failed conversation reads its reason and retry rule off the record", () => {
    const record = { last_run_error: "No AI model is set up yet.", last_run_error_code: "model_not_configured", last_run_retryable: true };
    const shown = runFailure("failed", null, record);
    assert.equal(shown.message, "No AI model is set up yet.");
    assert.equal(shown.noModel, true);
    assert.equal(shown.retryable, false, "the same run would fail the same way");

    const refused = runFailure("failed", null, { last_run_error: "It broke.", last_run_retryable: false });
    assert.deepEqual(refused, { message: "It broke.", noModel: false, retryable: false });
    assert.equal(runFailure("failed", null, {}).retryable, true, "a record without the field keeps the button");
});

test("a live stream failure wins over what the record said before it", () => {
    const stream = Object.assign(new Error("Provider down"), { code: "provider_error" });
    const shown = runFailure("failed", stream, { last_run_error_code: "model_not_configured" });
    assert.equal(shown.message, "Provider down");
    assert.equal(shown.noModel, false);
});

test("an idle conversation shows no stored failure", () => {
    assert.equal(runFailure("idle", null, { last_run_error: "old" }).message, null);
});

test("the no-model sentence names the teammate and where models are added", () => {
    assert.equal(noModelSentence("Ada"), "Ada has no model to think with yet. Add one in Settings → Models.");
});

test("a local provider the check did not find is not answering; a remote one is not judged", () => {
    const found = [{ baseUrl: "http://127.0.0.1:11434/v1" }];
    assert.equal(localRouteAnswering("http://localhost:11434/v1/", found), true);
    assert.equal(localRouteAnswering("http://127.0.0.1:1234/v1", found), false);
    assert.equal(localRouteAnswering("https://api.provider.test/v1", found), null);
    assert.equal(localRouteAnswering("http://127.0.0.1:1234/v1", undefined), null);
    assert.equal(isLocalRoute("http://[::1]:8080"), true);
});

test("a local route may be saved without a key; a remote one may not", () => {
    assert.equal(keyToSend("", "http://127.0.0.1:11434/v1", "lemma-local"), "lemma-local");
    assert.equal(keyToSend("", "https://api.provider.test/v1", "lemma-local"), "");
    assert.equal(keyToSend(" sk ", "http://127.0.0.1:11434/v1", "lemma-local"), "sk");
});

test("a failed Test says whether the key was refused or the list could not be read", async () => {
    const { testFailureMessage } = await import("../src/org/provider-draft.ts");
    assert.equal(testFailureMessage("Acme", "HTTP 401 Unauthorized"), "Acme rejected this API key.");
    assert.equal(
        testFailureMessage("Acme", "connection refused"),
        "Couldn't read the model list from Acme. Check the key, or type a model name below.",
    );
});

test("voice is offered only when the install says it is set up", async () => {
    const { voiceConfigured } = await import("../src/call/voice-config.ts");
    const answering = (body: unknown, ok = true) =>
        (async () => ({ ok, json: async () => body })) as unknown as typeof fetch;
    assert.equal(await voiceConfigured(answering({ configured: true })), true);
    assert.equal(await voiceConfigured(answering({ configured: false })), false);
    assert.equal(await voiceConfigured(answering({ configured: true }, false)), false);
    assert.equal(await voiceConfigured((async () => { throw new Error("offline"); }) as unknown as typeof fetch), false);
});

test("a refusal whose fix is in Settings → Models gets the way there", async () => {
    const { pointsAtModels } = await import("../src/thread/model-setup.ts");
    assert.equal(pointsAtModels("The model provider rejected this model's API key (HTTP 401). Check the key in Settings → Models."), true);
    assert.equal(pointsAtModels("The run hit a usage allowance."), false);
    assert.equal(pointsAtModels(null), false);
});

test("an agent that is not ready says where it is fixed, not just that it is unavailable", async () => {
    const { runtimeTrouble } = await import("../src/data/runtimes.ts");
    assert.equal(runtimeTrouble("h1", "UNAVAILABLE"), "Not ready on its computer");
    assert.equal(runtimeTrouble("h1", "UNAVAILABLE_FOR_YOU"), "Not shared with you");
});

test("a send that never reached Lemma is said as that, not as a raw fetch error", async () => {
    const { saidAboutSending, UNREACHABLE } = await import("../src/data/trouble.ts");
    assert.equal(saidAboutSending(new TypeError("Failed to fetch"), "That did not send."), UNREACHABLE);
    assert.equal(saidAboutSending(Object.assign(new Error("timed out"), { name: "NetworkError" }), "x"), UNREACHABLE);
    assert.equal(saidAboutSending(new Error("This conversation is not open yet."), "x"), "This conversation is not open yet.");
});
