/** What a chat can run on, read for the model picker: the parts of the web's
 *  `data/runtimes.ts` the picker needs (`readRuntime`, `readChoice`,
 *  `shortModel`), and the rule `model-picker.tsx` applies to choose what to
 *  offer. Pure, so the tests read it directly. */

export interface RuntimeModel {
    name: string;
    /** The readable end of the name: `anthropic/models/sonnet` is `sonnet`. */
    label: string;
}

export interface Runtime {
    id: string;
    name: string;
    models: RuntimeModel[];
    defaultModel: string;
    /** Retired: never offered. */
    archived: boolean;
}

export interface Choice {
    runtimeId: string;
    model: string;
}

function asString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

/** The readable end of a model name. `openai/models/gpt-5` is `gpt-5`, and a
 *  name with no slashes in it is already the answer. */
export function shortModel(name: string): string {
    const trimmed = name.replace(/\/$/, "");
    const marked = trimmed.match(/\/(?:models|routers)\/([^/]+)$/);
    if (marked?.[1]) return marked[1];
    return trimmed.split("/").filter(Boolean).at(-1) || trimmed;
}

function readModel(raw: unknown): RuntimeModel | null {
    const entry = (raw ?? {}) as { name?: string; display_name?: string | null };
    const name = asString(entry.name);
    if (!name) return null;
    return { name, label: asString(entry.display_name) || shortModel(name) };
}

/** One saved runtime profile, as `agentRuntime.listProfiles` answers it. */
export function readRuntime(raw: unknown): Runtime | null {
    const entry = (raw ?? {}) as {
        id?: string;
        name?: string;
        status?: string;
        harness_key?: string | null;
        default_model_name?: string | null;
        model_catalog?: unknown[];
    };
    const id = asString(entry.id);
    if (!id) return null;
    const models = (entry.model_catalog ?? []).map(readModel).filter((model): model is RuntimeModel => model !== null);
    const defaultModel = asString(entry.default_model_name);
    return {
        id,
        name: asString(entry.name) || asString(entry.harness_key) || id,
        /* A profile with no catalog still offers the one model it names. */
        models: models.length > 0 || !defaultModel ? models : [{ name: defaultModel, label: shortModel(defaultModel) }],
        defaultModel,
        archived: entry.status === "DISABLED",
    };
}

export function readChoice(raw: unknown): Choice | null {
    const entry = (raw ?? {}) as { profile_id?: string; model_name?: string | null };
    const runtimeId = asString(entry.profile_id);
    if (!runtimeId) return null;
    return { runtimeId, model: asString(entry.model_name) };
}

/** What the picker offers, by the web's rule.
 *
 *  The models of the runtime the chat runs on when nothing is chosen -- the
 *  Lemma's own pick, else the organization's -- since a model from a key the
 *  chat would not reach changes nothing. The first row says what "nothing
 *  chosen" means right now ("Default (sonnet)"), so nobody has to choose a
 *  model to find out what they already had. Fewer than two models is nothing
 *  to choose between: the picker then only names a choice already saved. */
export function modelChoices(
    runtimes: readonly Runtime[],
    podChoice: Choice | null,
    inherited: Choice | null,
    chosen: Choice | null,
): { runtime: Runtime | null; models: RuntimeModel[]; defaultLabel: string; chosenLabel: string; offered: boolean } {
    const live = runtimes.filter((one) => !one.archived);
    const fallback = podChoice ?? inherited ?? null;
    const runtime = fallback ? live.find((one) => one.id === fallback.runtimeId) ?? null : null;
    const models = runtime?.models ?? [];
    const resolved = fallback?.model || runtime?.defaultModel || "";
    /* The catalog's own name when it has one: "Sonnet 5.5" reads in the
       room a phone has, where the raw name is cut off. */
    const named = models.find((model) => model.name === resolved)?.label ?? shortModel(resolved);
    const defaultLabel = "Default (" + (named || "no model set") + ")";
    const chosenLabel = chosen ? models.find((model) => model.name === chosen.model)?.label ?? shortModel(chosen.model) : "";
    return { runtime, models, defaultLabel, chosenLabel, offered: models.length >= 2 };
}

/** The conversation's `agent_runtime`, as `conversations.update` takes it:
 *  sent whole, null included, since it is replaced rather than merged. */
export function runtimePayload(choice: Choice | null): { profile_id: string; model_name: string | null } | null {
    return choice ? { profile_id: choice.runtimeId, model_name: choice.model || null } : null;
}
