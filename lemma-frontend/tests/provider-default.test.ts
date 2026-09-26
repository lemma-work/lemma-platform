import test from "node:test";
import assert from "node:assert/strict";
import { canBeOrganizationDefault, firstProviderOffer } from "../src/org/provider-draft.ts";
import type { Runtime } from "../src/data/runtimes.ts";

function runtime(id: string, overrides: Partial<Runtime> = {}): Runtime {
    return {
        id,
        name: id,
        kind: "key",
        harness: "",
        harnessId: "",
        models: [{ name: id + "-1", label: id + "-1" }],
        defaultModel: id + "-1",
        selections: {},
        scope: "org",
        archived: false,
        trouble: "",
        ...overrides,
    };
}

/* ── who can be everyone's model ───────────────────────────────────── */

test("only a live, organization-wide key can be the default", () => {
    assert.equal(canBeOrganizationDefault(runtime("shared")), true);
    assert.equal(canBeOrganizationDefault(runtime("mine", { scope: "personal" })), false);
    assert.equal(canBeOrganizationDefault(runtime("built-in", { scope: "system" })), false);
    assert.equal(canBeOrganizationDefault(runtime("retired", { archived: true })), false);
    assert.equal(canBeOrganizationDefault(runtime("agent", { kind: "agent" })), false);
});

/* ── the offer after the first key ─────────────────────────────────── */

test("the first key added to an empty organization is offered", () => {
    const added = runtime("openrouter");

    assert.equal(firstProviderOffer([], [added], null), added);
});

test("a retired key does not count as something that could answer", () => {
    const retired = runtime("old", { archived: true });
    const added = runtime("new");

    assert.equal(firstProviderOffer([retired], [retired, added], null), added);
});

test("nothing is offered once a default has been chosen", () => {
    assert.equal(
        firstProviderOffer([], [runtime("openrouter")], { runtimeId: "elsewhere", model: "" }),
        null,
    );
});

test("nothing is offered when something could already answer", () => {
    const serverModel = runtime("system:lemma", { scope: "system" });

    assert.equal(firstProviderOffer([serverModel], [serverModel, runtime("openrouter")], null), null);
});

test("a key only one person can use is never offered to everyone", () => {
    assert.equal(firstProviderOffer([], [runtime("mine", { scope: "personal" })], null), null);
});

test("nothing is offered when the add did not land", () => {
    assert.equal(firstProviderOffer([], [], null), null);
});
