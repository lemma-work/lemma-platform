import test from "node:test";
import assert from "node:assert/strict";
import {
    agentOptions,
    agentSettingsChanges,
    agentHealth,
    agentLabel,
    agentModels,
    chosenModel,
    describeChoice,
    readChoice,
    readComputer,
    readLocalAgent,
    readRuntime,
    runtimeTrouble,
    shortModel,
    stillLooking,
    type Computer,
    type Runtime,
} from "../src/data/runtimes.ts";

/** The same class of failure `accounts.test.ts` exists for: every assertion
 *  here is about a field the API really sends, in the shape it really sends
 *  it. Getting one wrong produces a page that looks finished and says nothing
 *  true. */

test("a coding agent is HARNESS; which one it is comes from metadata", () => {
    /* There is no kind per tool. Reading `kind` for "is this Codex" would
       make Codex and Claude Code the same row. */
    const runtime = readRuntime({
        id: "p1",
        name: "Claude Code",
        kind: "HARNESS",
        scope: "PERSONAL",
        harness_id: "h1",
        metadata: { harness_key: "claude-code" },
        availability_status: "READY",
    });
    assert.equal(runtime?.kind, "agent");
    assert.equal(runtime?.harness, "claude-code");
    assert.equal(agentLabel("claude-code"), "Claude Code");
    assert.equal(runtime?.scope, "personal");
    assert.equal(runtime?.trouble, "");
});

test("a bought key is not a coding agent, and is never unreachable", () => {
    const runtime = readRuntime({
        id: "p2",
        name: "OpenRouter",
        kind: "MODEL_PROVIDER",
        scope: "ORGANIZATION",
        model_catalog: [{ name: "openai/models/gpt-5", display_name: null }],
    });
    assert.equal(runtime?.kind, "key");
    assert.equal(runtime?.harness, "");
    assert.equal(runtime?.models[0]?.label, "gpt-5");
    // No harness behind it, so availability has nothing to say.
    assert.equal(runtimeTrouble("", "OFFLINE"), "");
    assert.equal(runtimeTrouble("h1", "OFFLINE"), "Computer offline");
});

test("a runtime with no catalog still offers the model it names", () => {
    // Otherwise a working runtime draws as "0 models" and its row is
    // unpickable in a picker built from `models`.
    const runtime = readRuntime({
        id: "p3",
        name: "Anthropic",
        kind: "MODEL_PROVIDER",
        default_model_name: "claude-opus-5",
    });
    assert.deepEqual(runtime?.models, [{ name: "claude-opus-5", label: "claude-opus-5" }]);
});

test("archived is DISABLED, not a missing row", () => {
    const runtime = readRuntime({ id: "p4", name: "Old key", kind: "MODEL_PROVIDER", status: "DISABLED" });
    assert.equal(runtime?.archived, true);
});

test("models come out of the `model` config option, defensively", () => {
    // An open shape: a value is `value` or `id`, categories other than
    // `model` are somebody else's setting, and anything nameless is not a
    // choice a person can make.
    const models = agentModels([
        { category: "permission", options: [{ value: "plan", name: "Plan" }] },
        { category: "model", options: [{ value: "sonnet", name: "Sonnet" }, { id: "opus" }, { name: "nameless" }] },
    ]);
    assert.deepEqual(models, [
        { name: "sonnet", label: "Sonnet" },
        { name: "opus", label: "opus" },
    ]);
    assert.deepEqual(agentModels(undefined), []);
});

test("an agent wears the catalogue's name, and carries its fix", () => {
    const agent = readLocalAgent({
        id: "h1",
        harness_key: "codex",
        display_name: "codex-cli",
        health: "AUTH_REQUIRED",
        upstream_version: "0.9.1",
    });
    assert.equal(agent?.name, "Codex");
    assert.equal(agent?.ready, false);
    assert.equal(agent?.state, "Sign-in needed");
    assert.match(agent?.fix ?? "", /Sign in/);
});

test("an unknown health is a state, not a blank", () => {
    const health = agentHealth("SOMETHING_NEW");
    assert.equal(health.ready, false);
    assert.equal(health.state, "Something new");
    assert.notEqual(health.fix, "");
});

test("an empty agent list means 'still looking' only while looking is plausible", () => {
    const now = Date.parse("2026-09-15T12:00:00Z");
    const computer = readComputer({
        id: "c1",
        display_name: "Deepak's MacBook",
        status: "ONLINE",
        created_at: "2026-09-15T11:59:45Z",
    }) as Computer;
    assert.equal(stillLooking(computer, now), true);
    // Ten minutes later it has found none, and saying so is the honest answer.
    assert.equal(stillLooking(computer, now + 600_000), false);
    // A computer that is off publishes nothing, ever. It is not "looking".
    assert.equal(stillLooking({ ...computer, online: false }, now), false);
});

test("a key with no model names the model it will actually run", () => {
    /* The backend resolves an open model at dispatch — the runtime's default,
       else the first it offers. A page that prints "Default" instead is
       naming something that has a name. */
    const runtime: Runtime = {
        id: "k1", name: "OpenRouter", kind: "key", harness: "", harnessId: "",
        models: [{ name: "sonnet", label: "Sonnet" }, { name: "opus", label: "Opus" }],
        defaultModel: "", selections: {}, scope: "org", archived: false, trouble: "",
    };
    assert.equal(chosenModel(runtime, { runtimeId: "k1", model: "" }), "sonnet");
    assert.equal(chosenModel(runtime, { runtimeId: "k1", model: "opus" }), "opus");
    assert.equal(chosenModel({ ...runtime, defaultModel: "opus" }, { runtimeId: "k1", model: "" }), "opus");
    assert.equal(describeChoice([runtime], { runtimeId: "k1", model: "opus" }), "OpenRouter · Opus");
    // A runtime that has gone away describes as nothing rather than as its id.
    assert.equal(describeChoice([runtime], { runtimeId: "gone", model: "" }), "");
});

test("an unpinned coding agent runs its own default, and says so", () => {
    /* Dispatch sends no model for an unpinned agent, and a stale pin falls
       back to the agent's default too. The first model of its list is not
       what runs. */
    const runtime: Runtime = {
        id: "p1", name: "Claude Code", kind: "agent", harness: "claude-code", harnessId: "h1",
        models: [{ name: "sonnet", label: "Sonnet" }, { name: "opus", label: "Opus" }],
        defaultModel: "", selections: {}, scope: "personal", archived: false, trouble: "",
    };
    assert.equal(chosenModel(runtime, { runtimeId: "p1", model: "" }), "");
    assert.equal(describeChoice([runtime], { runtimeId: "p1", model: "" }), "Claude Code · Agent default");
    assert.equal(chosenModel(runtime, { runtimeId: "p1", model: "opus" }), "opus");
    assert.equal(chosenModel(runtime, { runtimeId: "p1", model: "retired-model" }), "");
    assert.equal(chosenModel({ ...runtime, defaultModel: "opus" }, { runtimeId: "p1", model: "" }), "opus");
});

test("an agent's options are read generically, effort and mode first", () => {
    const options = agentOptions([
        { id: "model", category: "model", options: [{ value: "gpt-5" }] },
        { id: "sandbox_extra", category: "other", options: [{ value: "on" }] },
        // OpenCode's effort; Codex calls it `reasoning_effort`. The category is shared.
        { id: "effort", category: "thought_level", name: "Effort", current_value: "high",
            options: [{ value: "low", name: "Low" }, { value: "high", name: "High" }, { value: "max", name: "Max" }] },
        // Plan mode is a value the host publishes, so it is a choice here.
        { id: "mode", category: "mode", current_value: "default", metadata: { policy: true },
            options: [{ value: "default", name: "Default" }, { value: "plan", name: "Plan" }] },
        { id: "empty", category: "thought_level", options: [] },
    ]);
    assert.deepEqual(options.map((option) => [option.key, option.kind]), [
        ["effort", "effort"],
        ["mode", "mode"],
        ["sandbox_extra", "other"],
    ]);
    assert.equal(options[0].current, "high");
    assert.deepEqual(options[1].choices.map((choice) => choice.value), ["default", "plan"]);
});

test("a saved agent's selections and an agent's own default model are read", () => {
    const runtime = readRuntime({
        id: "p1", kind: "HARNESS", harness_id: "h1",
        config: { harness_snapshot_revision: "r1", config_selections: { effort: "max", odd: 3 } },
    });
    assert.deepEqual(runtime?.selections, { effort: "max" });
    const agent = readLocalAgent({
        id: "h1", harness_key: "opencode", health: "READY",
        config_options: [{ id: "model", category: "model", current_value: "qwen3-coder", options: [{ value: "qwen3-coder" }] }],
    });
    assert.equal(agent?.defaultModel, "qwen3-coder");
});

test("a coding agent's settings send only what changed", () => {
    const before = { model: "", selections: { effort: "high" } };
    assert.deepEqual(agentSettingsChanges(before, before), {});
    assert.deepEqual(agentSettingsChanges(before, { ...before, model: "opus" }), { default_model_name: "opus" });
    assert.deepEqual(agentSettingsChanges({ ...before, model: "opus" }, before), { default_model_name: null });
    // Selections replace wholesale, and "as on that computer" is no key at all.
    assert.deepEqual(
        agentSettingsChanges(before, { model: "", selections: { effort: "", mode: "plan" } }),
        { config_selections: { mode: "plan" } },
    );
});

test("a stored choice is profile_id and model_name", () => {
    assert.deepEqual(readChoice({ profile_id: "p1", model_name: "sonnet" }), { runtimeId: "p1", model: "sonnet" });
    // The catalog's own default is a bare profile id — legal, and common.
    assert.deepEqual(readChoice({ profile_id: "system:lemma" }), { runtimeId: "system:lemma", model: "" });
    assert.equal(readChoice({ model_name: "sonnet" }), null);
    assert.equal(readChoice(null), null);
});

test("a model name reads as its short end", () => {
    assert.equal(shortModel("anthropic/models/claude-sonnet-5"), "claude-sonnet-5");
    assert.equal(shortModel("openai/routers/auto"), "auto");
    assert.equal(shortModel("gpt-5"), "gpt-5");
});
