import test from "node:test";
import assert from "node:assert/strict";
import { needsAiModel } from "../src/thread/model-setup.ts";
import {
    chosenVisionModels,
    discoveryRequest,
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
