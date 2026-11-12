import type { ModelsSource } from "./source";
import {
    readChoice, readComputer, readLocalAgent, readRuntime, type Choice, type Computer, type LocalAgent, type Runtime,
} from "@web/data/runtimes";

const MIN = 60_000;
const DAY = 24 * 60 * MIN;

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/* What Acme runs on, wire-shaped so the sample goes through the same readers
   as a live account. One of every state the screens must say: a built-in
   model, the organization's own key (its default, and the key the chat's
   sample answers on), a second key whose test fails, a retired key, a coding
   agent on this person's laptop, a shared one on a computer that is asleep,
   and a colleague's shared agent on a computer not listed here. No key is
   ever held: the server never sends one back. */

let ORG_DEFAULT: Choice | null = { runtimeId: "sample-runtime", model: "" };

let RUNTIMES: Record<string, unknown>[] = [
    {
        id: "sample-lemma", name: "Lemma", kind: "MODEL_PROVIDER", scope: "SYSTEM", status: "ACTIVE",
        default_model_name: "anthropic/models/claude-haiku-5-5",
        model_catalog: [{ name: "anthropic/models/claude-haiku-5-5", display_name: "Haiku 5.5" }],
    },
    {
        id: "sample-runtime", name: "Anthropic", kind: "MODEL_PROVIDER", scope: "ORGANIZATION", status: "ACTIVE",
        default_model_name: "anthropic/models/claude-sonnet-5-5", config: { base_url: "https://api.anthropic.com" },
        model_catalog: [
            { name: "anthropic/models/claude-sonnet-5-5", display_name: "Sonnet 5.5", capabilities: ["TEXT", "VISION", "REASONING"] },
            { name: "anthropic/models/claude-opus-5-5", display_name: "Opus 5.5", capabilities: ["TEXT", "VISION", "REASONING"] },
            { name: "anthropic/models/claude-haiku-5-5", display_name: "Haiku 5.5", capabilities: ["TEXT", "VISION"] },
        ],
    },
    {
        id: "sample-openrouter", name: "OpenRouter", kind: "MODEL_PROVIDER", scope: "ORGANIZATION", status: "ACTIVE",
        default_model_name: "deepseek/deepseek-chat", config: { base_url: "https://openrouter.ai/api/v1" },
        model_catalog: [
            { name: "deepseek/deepseek-chat", display_name: "DeepSeek Chat" },
            { name: "meta-llama/llama-4-70b", display_name: "Llama 4 70B" },
            { name: "qwen/qwen3-coder" },
        ],
    },
    {
        id: "sample-old-key", name: "Last year's key", kind: "MODEL_PROVIDER", scope: "ORGANIZATION", status: "DISABLED",
        default_model_name: "gpt-4o",
    },
    {
        id: "sample-claude-code", name: "Claude Code", kind: "HARNESS", scope: "PERSONAL", status: "ACTIVE",
        harness_id: "h-claude", availability_status: "READY", default_model_name: null, harness_key: "claude-code",
        config: { config_selections: { effort: "high" } },
        model_catalog: [{ name: "sonnet", display_name: "Sonnet" }, { name: "opus", display_name: "Opus" }],
    },
    {
        id: "sample-opencode", name: "OpenCode", kind: "HARNESS", scope: "ORGANIZATION", status: "ACTIVE",
        harness_id: "h-opencode", availability_status: "OFFLINE", default_model_name: "qwen3-coder", harness_key: "opencode",
        model_catalog: [{ name: "qwen3-coder", display_name: "Qwen3 Coder" }],
    },
    {
        id: "sample-sam-codex", name: "Sam's Codex", kind: "HARNESS", scope: "ORGANIZATION", status: "ACTIVE",
        harness_id: "h-sam-codex", availability_status: "READY", default_model_name: "gpt-5-codex", harness_key: "codex",
        model_catalog: [{ name: "gpt-5-codex", display_name: "GPT-5 Codex" }],
    },
];

const COMPUTERS: Record<string, unknown>[] = [
    {
        id: "c-laptop", display_name: "Ada's laptop", status: "ONLINE", host_release: "0.9.2",
        created_at: new Date(Date.now() - 30 * DAY).toISOString(), last_seen_at: new Date(Date.now() - MIN).toISOString(),
    },
    {
        id: "c-studio", display_name: "Studio Mac mini", status: "OFFLINE", host_release: "0.9.1",
        created_at: new Date(Date.now() - 60 * DAY).toISOString(), last_seen_at: new Date(Date.now() - DAY - 3 * 60 * MIN).toISOString(),
    },
];

const CLAUDE_EFFORT = {
    id: "effort", category: "thought_level", name: "Effort", current_value: "medium",
    options: [{ value: "low", name: "Low" }, { value: "medium", name: "Medium" }, { value: "high", name: "High" }],
};
const CLAUDE_EFFORT_MAX = { ...CLAUDE_EFFORT, options: [...CLAUDE_EFFORT.options, { value: "max", name: "Max" }] };
const CLAUDE_MODE = {
    id: "mode", category: "mode", name: "Mode", current_value: "default", metadata: { policy: true },
    options: [{ value: "default", name: "Ask before edits" }, { value: "plan", name: "Plan" }],
};

const AGENTS_ON: Record<string, Record<string, unknown>[]> = {
    "c-laptop": [
        {
            id: "h-claude", harness_key: "claude-code", display_name: "Claude Code", health: "READY", upstream_version: "2.4.12",
            config_options: [
                {
                    id: "model", category: "model", current_value: "sonnet",
                    /* Opus publishes its own set, with an effort Sonnet lacks. */
                    options: [{ value: "sonnet", name: "Sonnet" }, { value: "opus", name: "Opus", lemma_options: [CLAUDE_EFFORT_MAX, CLAUDE_MODE] }],
                },
                CLAUDE_EFFORT,
                CLAUDE_MODE,
            ],
        },
        {
            /* Installed and signed out. */
            id: "h-codex", harness_key: "codex", display_name: "codex", health: "AUTH_REQUIRED", upstream_version: "0.52.0",
            config_options: [{ category: "model", options: [{ value: "gpt-5-codex", name: "GPT-5 Codex" }] }],
        },
        {
            /* Ready, and nobody has added it. */
            id: "h-cursor", harness_key: "cursor", display_name: "Cursor", health: "READY", upstream_version: "1.9.3",
            config_options: [{ category: "model", options: [{ value: "auto", name: "Auto" }, { value: "sonnet-4.5", name: "Sonnet 4.5" }] }],
        },
    ],
    "c-studio": [
        {
            id: "h-opencode", harness_key: "opencode", display_name: "OpenCode", health: "READY", upstream_version: "1.6.0",
            config_options: [
                { id: "model", category: "model", current_value: "qwen3-coder", options: [{ value: "qwen3-coder", name: "Qwen3 Coder" }] },
                {
                    id: "effort", category: "thought_level", name: "Effort", current_value: "high",
                    options: [{ value: "low", name: "Low" }, { value: "high", name: "High" }, { value: "max", name: "Max" }],
                },
            ],
        },
    ],
};

/** Books runs on the shared OpenCode, whose computer is asleep: the state a
 *  Lemma's model screen has to say out loud. The rest follow Acme. */
const LEMMA_CHOICE: Record<string, Record<string, unknown>> = {
    "sample-books-986": { profile_id: "sample-opencode", model_name: null },
};

const read = () => RUNTIMES.map(readRuntime).filter((one): one is Runtime => one !== null);

/** Writes change Acme's list in memory, until the app closes. */
export function modelsSample(): ModelsSource {
    return {
        runtimes: async () => {
            await pause(110);
            return read();
        },
        runtime: async (_orgId, runtimeId) => {
            await pause(90);
            const raw = RUNTIMES.find((one) => one.id === runtimeId);
            if (!raw) return null;
            /* The detail's live harness, from the same sample computers. */
            const harness = raw.harness_id ? Object.values(AGENTS_ON).flat().find((one) => one.id === raw.harness_id) : undefined;
            return readRuntime(harness ? { ...raw, harness } : raw);
        },
        inherited: async () => {
            await pause(60);
            return ORG_DEFAULT ?? { runtimeId: "sample-lemma", model: "" };
        },
        organizationDefault: async () => {
            await pause(60);
            return ORG_DEFAULT;
        },
        setOrganizationDefault: async (_orgId, choice) => {
            await pause(300);
            ORG_DEFAULT = choice;
        },
        test: async (_orgId, runtimeId) => {
            await pause(800);
            const found = read().find((one) => one.id === runtimeId);
            if (!found) return { ok: false, message: "That provider is gone.", models: null };
            /* OpenRouter's sample key is refused, so a failed test can be seen. */
            if (runtimeId === "sample-openrouter") return { ok: false, message: "The provider refused this API key. Check it, or connect a new one.", models: null };
            return { ok: true, message: found.name + " answered.", models: found.models.map((model) => model.name) };
        },
        addKey: async (_orgId, key) => {
            await pause(500);
            /* The key is not kept: nothing here would ever read it back. */
            RUNTIMES = [...RUNTIMES, {
                id: "sample-key-" + (RUNTIMES.length + 1),
                name: key.name, kind: "MODEL_PROVIDER", scope: "ORGANIZATION", status: "ACTIVE",
                default_model_name: key.models[0] ?? null,
                config: { base_url: key.baseUrl },
                model_catalog: key.models.map((name) => ({
                    name,
                    capabilities: key.protocol === "anthropic" || key.visionModels.includes(name) ? ["TEXT", "VISION"] : ["TEXT"],
                })),
            }];
        },
        archive: async (_orgId, runtimeId) => {
            await pause(300);
            /* Retiring the default un-chooses it, as the server does. */
            if (ORG_DEFAULT?.runtimeId === runtimeId) ORG_DEFAULT = null;
            RUNTIMES = RUNTIMES.map((one) => (one.id === runtimeId ? { ...one, status: "DISABLED" } : one));
        },
        restore: async (_orgId, runtimeId) => {
            await pause(300);
            RUNTIMES = RUNTIMES.map((one) => (one.id === runtimeId ? { ...one, status: "ACTIVE" } : one));
        },
        computers: async () => {
            await pause(140);
            return COMPUTERS.map(readComputer)
                .filter((one): one is Computer => one !== null)
                .map((computer) => ({
                    ...computer,
                    agents: (AGENTS_ON[computer.id] ?? []).map(readLocalAgent).filter((agent): agent is LocalAgent => agent !== null),
                }));
        },
        lemmaChoice: async (podId) => {
            await pause(80);
            return readChoice(LEMMA_CHOICE[podId]);
        },
        setLemmaChoice: async (podId, choice) => {
            await pause(350);
            if (!choice) {
                delete LEMMA_CHOICE[podId];
                return;
            }
            LEMMA_CHOICE[podId] = {
                profile_id: choice.runtimeId,
                model_name: choice.model || null,
                ...(choice.selections && Object.keys(choice.selections).length ? { config_selections: choice.selections } : {}),
            };
        },
    };
}
