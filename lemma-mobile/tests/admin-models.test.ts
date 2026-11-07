import { describe, expect, it } from "vitest";
import type { LiveContext } from "../src/areas/context";
import { modelsLive } from "../src/areas/admin/models/live";
import {
    PRESETS, agentRow, agentSetTo, carried, chosenOptions, computerPoll, computerWords, countWords, defaultChoices, isPicked,
    keyRow, kindOf, ledger, lemmaModelSaid, pickRows, sameName, shownRows, testWords, testable, whoPicks, withSelection,
} from "../src/areas/admin/models/model";
import { modelsSample } from "../src/areas/admin/models/sample";
import { firstProviderOffer } from "@web/org/provider-draft";
import { modelSetupState } from "@web/shell/runs-on-state";
import { readComputer, readLocalAgent, readRuntime, type Computer, type LocalAgent, type Runtime } from "@web/data/runtimes";

/** Models (PARITY.md, F): what the Models screens and a Lemma's model screen decide. */

const BANNED = /\b(teammate|pod|bot|channel|goal|mission|line)s?\b/i;

function runtime(raw: Record<string, unknown>): Runtime {
    const read = readRuntime({ status: "ACTIVE", kind: "MODEL_PROVIDER", scope: "ORGANIZATION", ...raw });
    if (!read) throw new Error("unreadable");
    return read;
}

const anthropic = runtime({ id: "k-anthropic", name: "Anthropic", default_model_name: "a/models/sonnet", model_catalog: [{ name: "a/models/sonnet", display_name: "Sonnet 5.5" }, { name: "a/models/opus", display_name: "Opus 5.5" }] });
const builtIn = runtime({ id: "k-lemma", name: "Lemma", scope: "SYSTEM", default_model_name: "a/models/haiku" });
const retired = runtime({ id: "k-old", name: "Last year's key", status: "DISABLED", default_model_name: "gpt-4o" });
const claude = runtime({
    id: "r-claude", name: "Claude Code", kind: "HARNESS", scope: "PERSONAL", harness_id: "h-claude", harness_key: "claude-code",
    availability_status: "READY", config: { config_selections: { effort: "high" } },
    model_catalog: [{ name: "sonnet", display_name: "Sonnet" }, { name: "opus", display_name: "Opus" }],
});
const asleep = runtime({ id: "r-open", name: "OpenCode", kind: "HARNESS", harness_id: "h-open", harness_key: "opencode", availability_status: "OFFLINE", default_model_name: "qwen3-coder" });
const colleague = runtime({ id: "r-codex", name: "Sam's Codex", kind: "HARNESS", harness_id: "h-sam", harness_key: "codex", availability_status: "READY", default_model_name: "gpt-5-codex" });

const EFFORT = { id: "effort", category: "thought_level", name: "Effort", current_value: "medium", options: [{ value: "low", name: "Low" }, { value: "medium", name: "Medium" }, { value: "high", name: "High" }] };
const claudeAgent = readLocalAgent({
    id: "h-claude", harness_key: "claude-code", display_name: "Claude Code", health: "READY", upstream_version: "2.4.12",
    config_options: [
        { id: "model", category: "model", current_value: "sonnet", options: [{ value: "sonnet", name: "Sonnet" }, { value: "opus", name: "Opus", lemma_options: [{ ...EFFORT, options: [...EFFORT.options, { value: "max", name: "Max" }] }] }] },
        EFFORT,
    ],
}) as LocalAgent;
const signedOut = readLocalAgent({ id: "h-codex", harness_key: "codex", display_name: "codex", health: "AUTH_REQUIRED" }) as LocalAgent;
const laptop: Computer = { ...(readComputer({ id: "c-1", display_name: "Ada's laptop", status: "ONLINE", host_release: "0.9.2" }) as Computer), agents: [claudeAgent, signedOut] };
const studio: Computer = {
    ...(readComputer({ id: "c-2", display_name: "Studio", status: "OFFLINE", host_release: "0.9.1", last_seen_at: "2026-10-23T19:12:00" }) as Computer),
    agents: [readLocalAgent({ id: "h-open", harness_key: "opencode", display_name: "OpenCode", health: "READY" }) as LocalAgent],
};

describe("the organization's list", () => {
    const all = [builtIn, anthropic, retired, claude, asleep, colleague];

    it("draws each thing once, where it comes from", () => {
        const book = ledger(all, [laptop, studio]);
        expect(book.keys.map((one) => one.id)).toEqual(["k-lemma", "k-anthropic", "k-old"]);
        expect(book.elsewhere.map((one) => one.id)).toEqual(["r-codex"]);
        expect(book.savedByAgent.get("h-claude")?.id).toBe("r-claude");
        expect(book.retired).toBe(1);
        expect(book.available).toBe(4);
        expect(book.troubled).toBe(1);
        expect(countWords(book.available, book.troubled)).toBe("4 to pick from · 1 needing attention");
        expect(countWords(0, 0)).toBe("Nothing to pick from");
    });

    it("says a key's kind and state on one row", () => {
        expect(keyRow(builtIn, false)).toEqual({ hint: "Built in · 1 model", value: "Available", tone: "ok" });
        expect(keyRow(anthropic, true)).toEqual({ hint: "Shared key · 2 models", value: "Default", tone: "ok" });
        expect(keyRow(retired, false).value).toBe("Retired");
        expect(keyRow(asleep, false)).toEqual({ hint: "Shared · 1 model", value: "Computer offline", tone: "warn" });
        expect(kindOf(claude)).toBe("Yours");
    });

    it("tests only the organization's own live keys", () => {
        expect(testable(anthropic)).toBe(true);
        expect(testable(builtIn)).toBe(false);
        expect(testable(retired)).toBe(false);
        expect(testable(claude)).toBe(false);
        expect(testWords({ ok: true, message: "Anthropic answered.", models: ["a", "b"] })).toBe("Anthropic answered. It lists 2 models.");
        expect(testWords({ ok: false, message: "The provider refused this API key.", models: null })).toBe("The provider refused this API key.");
    });

    it("offers only shared, live keys as everyone's default", () => {
        expect(defaultChoices(all)).toEqual([{ value: "k-anthropic", label: "Anthropic", hint: "Uses Sonnet 5.5 unless told otherwise" }]);
    });

    it("offers the first key as the default only when nothing could answer before", () => {
        expect(firstProviderOffer([], [anthropic], null)?.id).toBe("k-anthropic");
        expect(firstProviderOffer([builtIn], [builtIn, anthropic], null)).toBeNull();
        expect(firstProviderOffer([], [anthropic], { runtimeId: "k-anthropic", model: "" })).toBeNull();
    });
});

describe("coding agents, read only", () => {
    it("says what each can do now", () => {
        expect(agentRow(claudeAgent, laptop, claude)).toMatchObject({ name: "Claude Code", value: "Available", tone: "ok", added: true, hint: "Yours · 2 models" });
        expect(agentRow(signedOut, laptop, null)).toMatchObject({ name: "Codex", value: "Sign-in needed", tone: "warn", added: false });
        expect(agentRow(studio.agents[0], studio, asleep)).toMatchObject({ value: "Computer offline", tone: "muted" });
        const cursor = readLocalAgent({ id: "h-cursor", harness_key: "cursor", health: "READY" }) as LocalAgent;
        expect(agentRow(cursor, laptop, null).value).toBe("Not added yet");
        expect(agentRow(claudeAgent, laptop, { ...claude, archived: true }).value).toBe("Retired");
    });

    it("says what an added agent is set to, as chosen or as its computer has it", () => {
        expect(agentSetTo(claudeAgent, claude)).toEqual([
            { label: "Model", value: "Agent default (sonnet)" },
            { label: "Effort", value: "High" },
        ]);
        expect(agentSetTo(claudeAgent, { ...claude, selections: {} })[1]).toEqual({ label: "Effort", value: "As set there (Medium)" });
        /* Opus publishes its own set, with Max in it. */
        expect(agentSetTo(claudeAgent, { ...claude, defaultModel: "opus", selections: { effort: "max" } })).toEqual([
            { label: "Model", value: "Opus" },
            { label: "Effort", value: "Max" },
        ]);
        expect(agentSetTo(null, colleague)).toEqual([{ label: "Model", value: "gpt-5-codex" }]);
        expect(whoPicks(claude, "Acme")).toBe("Only you");
        expect(whoPicks(colleague, "Acme")).toBe("Everyone in Acme");
    });

    it("says a computer's version, and when an asleep one was last seen", () => {
        expect(computerWords(laptop)).toBe("Lemma app 0.9.2");
        expect(computerWords(studio, new Date("2026-10-23T22:00:00"))).toBe("Lemma app 0.9.1 · last seen 19:12");
        expect(computerWords(studio, new Date("2026-10-25T09:00:00"))).toBe("Lemma app 0.9.1 · last seen 23 October");
    });

    it("looks again quickly only while a fresh computer is still finding its agents", () => {
        const now = Date.parse("2026-10-24T12:00:00Z");
        const fresh: Computer = { ...laptop, agents: [], pairedAt: "2026-10-24T11:59:40Z" };
        expect(computerPoll(undefined, now)).toBe(4_000);
        expect(computerPoll([fresh], now)).toBe(4_000);
        expect(computerPoll([laptop], now)).toBe(20_000);
        expect(computerPoll([laptop, studio], now)).toBe(30_000);
        expect(computerPoll([], now)).toBe(30_000);
    });
});

describe("what a Lemma runs on", () => {
    const live = [anthropic, claude];

    it("offers every model, and an unpinned agent's own default first", () => {
        const rows = pickRows([...live, runtime({ id: "k-bare", name: "Bare" })]);
        expect(rows.map((row) => row.label)).toEqual(["Sonnet 5.5", "Opus 5.5", "Agent default", "Sonnet", "Opus", "Its usual model"]);
        expect(rows[2].choice).toEqual({ runtimeId: "r-claude", model: "" });
        expect(sameName("Claude Sonnet 5", "anthropic/models/claude-sonnet-5")).toBe(true);
        expect(sameName("Sonnet 5.5", "a/models/sonnet")).toBe(false);
    });

    it("ticks the model an open pick resolves to", () => {
        const open = { runtimeId: "k-anthropic", model: "" };
        expect(isPicked(anthropic, open, { runtimeId: "k-anthropic", model: "a/models/sonnet" })).toBe(true);
        expect(isPicked(anthropic, open, { runtimeId: "k-anthropic", model: "a/models/opus" })).toBe(false);
        expect(isPicked(anthropic, null, { runtimeId: "k-anthropic", model: "a/models/sonnet" })).toBe(false);
    });

    it("keeps only the options the newly picked model still offers", () => {
        const sets = { options: claudeAgent.options, byModel: claudeAgent.optionsByModel };
        const before = { runtimeId: "r-claude", model: "opus", selections: { effort: "max" } };
        expect(carried({ runtimeId: "r-claude", model: "sonnet" }, before, claude, sets)).toEqual({ runtimeId: "r-claude", model: "sonnet" });
        expect(carried({ runtimeId: "r-claude", model: "opus" }, { ...before, model: "sonnet", selections: { effort: "high" } }, claude, sets))
            .toEqual({ runtimeId: "r-claude", model: "opus", selections: { effort: "high" } });
        expect(carried({ runtimeId: "k-anthropic", model: "" }, before, claude, sets)).toEqual({ runtimeId: "k-anthropic", model: "" });
        expect(chosenOptions(claude, before, sets).map((one) => one.choices.length)).toEqual([4]);
        expect(chosenOptions(anthropic, { runtimeId: "k-anthropic", model: "" }, sets)).toEqual([]);
    });

    it("sets one option, or clears it back to the agent's own", () => {
        const choice = { runtimeId: "r-claude", model: "", selections: { effort: "high" } };
        expect(withSelection(choice, "effort", "low")).toEqual({ ...choice, selections: { effort: "low" } });
        expect(withSelection(choice, "effort", "")).toEqual({ runtimeId: "r-claude", model: "" });
    });

    it("cuts a long list but keeps the chosen model in view", () => {
        const many = runtime({ id: "k-many", name: "Many", model_catalog: Array.from({ length: 30 }, (_, n) => ({ name: "m-" + n })) });
        const rows = pickRows([many]);
        const cut = shownRows(rows, false, (row) => row.choice.model === "m-25");
        expect(cut.rows).toHaveLength(13);
        expect(cut.rows.at(-1)?.choice.model).toBe("m-25");
        expect(cut.hidden).toBe(17);
        expect(shownRows(rows, true, () => false).hidden).toBe(0);
    });

    it("says it in one phrase, and when there is nothing to run on", () => {
        const inherited = { runtimeId: "k-anthropic", model: "" };
        expect(lemmaModelSaid(live, null, inherited, null)).toBe("Organization default: Anthropic · Sonnet 5.5");
        expect(lemmaModelSaid(live, { runtimeId: "r-claude", model: "" }, inherited, null)).toBe("Claude Code · Agent default");
        expect(lemmaModelSaid(live, { runtimeId: "gone", model: "" }, inherited, null)).toBe("What it was set to is gone");
        expect(modelSetupState([], inherited, null)).toBe("none");
        expect(modelSetupState(live, { runtimeId: "missing", model: "" }, null)).toBe("default-missing");
        expect(lemmaModelSaid(live, null, { runtimeId: "missing", model: "" }, "default-missing")).toBe("No model yet: pick one");
        for (const said of [lemmaModelSaid(live, null, null, null), lemmaModelSaid(live, null, inherited, "none")]) expect(said).not.toMatch(BANNED);
    });
});

describe("connecting a key, live", () => {
    function capture() {
        const sent: unknown[] = [];
        const client = { agentRuntime: { createProfile: async (_orgId: string, payload: unknown) => { sent.push(payload); } } };
        return { sent, context: { client: () => client } as unknown as LiveContext };
    }

    it("names the kind of route, which the server tells the two apart by", async () => {
        const { sent, context } = capture();
        const live = modelsLive(context);
        await live.addKey("org-1", { protocol: "anthropic", name: "Anthropic", baseUrl: "", apiKey: "sample-not-a-key", models: [], visionModels: [] });
        await live.addKey("org-1", { protocol: "openai", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", apiKey: "sample-not-a-key", models: ["m-1", "m-2"], visionModels: ["m-2"] });
        expect(sent).toEqual([
            { name: "Anthropic", api_key: "sample-not-a-key", default_model_name: null, model_names: [], source: "ANTHROPIC_COMPATIBLE", base_url: null },
            { name: "OpenRouter", api_key: "sample-not-a-key", default_model_name: "m-1", model_names: ["m-1", "m-2"], source: "OPENAI_COMPATIBLE", base_url: "https://openrouter.ai/api/v1", vision_model_names: ["m-2"] },
        ]);
    });

    it("prefills only real provider routes", () => {
        for (const preset of PRESETS) expect(preset.baseUrl).toMatch(/^https:\/\//);
    });
});

describe("Acme's models, as sample data", () => {
    it("reads through the same readers as a live account", async () => {
        const source = modelsSample();
        const all = await source.runtimes("sample-acme");
        const machines = await source.computers();
        const book = ledger(all, machines);
        expect(book.keys.map((one) => one.name)).toEqual(["Lemma", "Anthropic", "OpenRouter", "Last year's key"]);
        expect(book.elsewhere.map((one) => one.name)).toEqual(["Sam's Codex"]);
        expect(machines.map((one) => one.agents.length)).toEqual([3, 1]);
        expect(await source.organizationDefault("sample-acme")).toEqual({ runtimeId: "sample-runtime", model: "" });
        expect(await source.lemmaChoice("sample-books-986")).toEqual({ runtimeId: "sample-opencode", model: "" });
        expect((await source.runtime("sample-acme", "sample-claude-code"))?.options?.byModel.opus?.[0].choices.length).toBe(4);
    });

    it("retiring the default un-chooses it, and a test can fail", async () => {
        const source = modelsSample();
        expect((await source.test("sample-acme", "sample-openrouter")).ok).toBe(false);
        expect((await source.test("sample-acme", "sample-runtime")).ok).toBe(true);
        await source.archive("sample-acme", "sample-runtime");
        expect(await source.organizationDefault("sample-acme")).toBeNull();
        expect(await source.inherited("sample-acme")).toEqual({ runtimeId: "sample-lemma", model: "" });
        await source.restore("sample-acme", "sample-runtime");
        await source.setOrganizationDefault("sample-acme", { runtimeId: "sample-runtime", model: "" });
    });
});
