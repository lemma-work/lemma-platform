/** What a teammate thinks with, and where that thinking happens.
 *
 *  Two things share this file because the API treats them as one thing. A
 *  bought API key and Claude Code on somebody's laptop are both *runtime
 *  profiles*: same list, same id, same shape, same slot in a conversation.
 *  There is no `HarnessKind` per coding tool — Codex, Claude Code, Cursor and
 *  OpenCode all arrive as `HARNESS`, and which one it is comes from
 *  `metadata.harness_key`. So the ledger is one list, and a computer is a
 *  heading inside it rather than a second list beside it.
 *
 *  Pure, and tested, for the reason `accounts.ts` is: every field here is a
 *  wire field, and reading one that does not exist fails silently — a name
 *  that falls back to an id, a status that never matches, a model list that is
 *  always empty. Those are the mistakes a screenshot cannot catch. */

/** One pickable model inside a runtime. */
export interface RuntimeModel {
    name: string;
    /** The short, readable end of the name — `anthropic/models/sonnet` is
     *  `sonnet`. The full name is what gets sent; this is what gets read. */
    label: string;
}

/** A row in the ledger: something a conversation can actually run on. */
export interface Runtime {
    id: string;
    name: string;
    /** `key` is a provider this organization pays for; `agent` is a coding
     *  agent on a paired computer. The difference is worth drawing because it
     *  is the difference between a bill and a machine that can be asleep. */
    kind: "key" | "agent";
    /** `claude-code`, `codex`, … Empty for a provider key. */
    harness: string;
    /** The live harness this profile was made from, when it has one. */
    harnessId: string;
    models: RuntimeModel[];
    defaultModel: string;
    scope: "system" | "org" | "personal";
    /** Retired: out of the picker, still readable, restorable. */
    archived: boolean;
    /** Why it cannot take work right now, in a person's words. Empty when it
     *  can. A provider key is always reachable, so it never fills this in. */
    trouble: string;
}

/** One coding agent as a computer reports it, before anyone has added it. */
export interface LocalAgent {
    /** The harness id — what a profile is created from. */
    id: string;
    harness: string;
    name: string;
    version: string;
    models: RuntimeModel[];
    /** READY means this computer would take a run right now. */
    ready: boolean;
    /** The state, said the way a person would say it. */
    state: string;
    /** What to do about it, when there is something to do. */
    fix: string;
}

/** A paired computer, and the agents it found on itself. */
export interface Computer {
    id: string;
    name: string;
    online: boolean;
    status: string;
    release: string;
    lastSeen: string;
    /** Empty while it is still looking — see `stillLooking`. */
    agents: LocalAgent[];
    pairedAt: string;
}

/** A stored choice: which runtime, and which of its models.
 *
 *  `model` may be empty, which is not the same as unset — it means "whatever
 *  that runtime runs by default", which the backend resolves at dispatch. */
export interface Choice {
    runtimeId: string;
    model: string;
}

/* ── the four agents Lemma drives ──────────────────────────────────── */

/** Keyed by the `harness_key` a paired computer publishes. A key that is not
 *  here still draws a row — it simply wears the generic mark and its own
 *  name, which is the honest answer for an agent this app has not been taught
 *  about yet. */
const AGENTS: Record<string, { label: string; logo: string }> = {
    "claude-code": { label: "Claude Code", logo: "/agent-logos/claudecode.png" },
    codex: { label: "Codex", logo: "/agent-logos/codex.png" },
    cursor: { label: "Cursor", logo: "/agent-logos/cursor.png" },
    opencode: { label: "OpenCode", logo: "/agent-logos/opencode.png" },
};

export function agentLabel(harness: string): string {
    return AGENTS[harness]?.label ?? "";
}

export function agentLogo(harness: string): string {
    return AGENTS[harness]?.logo ?? "";
}

/* ── health, said out loud ─────────────────────────────────────────── */

/** A computer probes each agent itself, so every non-ready state is fixed
 *  over there rather than here. The fix travels with the label — an unusable
 *  agent that reads as an opaque code is a dead end on the page. */
const HEALTH: Record<string, { state: string; fix: string }> = {
    READY: { state: "Ready", fix: "" },
    AUTH_REQUIRED: {
        state: "Sign-in needed",
        fix: "Sign in to this agent on that computer, then let it look again.",
    },
    INSTALLING: {
        state: "Setting up",
        fix: "That computer is starting it to see what it offers. Usually under a minute.",
    },
    UNSUPPORTED_VERSION: {
        state: "Version unsupported",
        fix: "Update this agent on that computer to a release Lemma supports.",
    },
    CONFIG_INVALID: {
        state: "Configuration invalid",
        fix: "Its settings on that computer were rejected. Fix them there.",
    },
    PROBE_FAILED: {
        state: "Could not start",
        fix: "That computer could not start it. Check the Lemma app's log there.",
    },
    DISABLED: { state: "Disabled", fix: "Turned off in the Lemma app on that computer." },
};

export function agentHealth(health: string): { state: string; fix: string; ready: boolean } {
    const known = HEALTH[health];
    if (known) return { ...known, ready: health === "READY" };
    return {
        state: sentence(health),
        fix: "That computer reported a state this app does not recognise yet.",
        ready: false,
    };
}

const HOST_STATUS: Record<string, string> = {
    ONLINE: "Online",
    OFFLINE: "Offline",
    DRAINING: "Finishing up",
    UPGRADE_REQUIRED: "Needs updating",
    REVOKED: "Removed",
};

export function computerStatus(status: string): string {
    return HOST_STATUS[status] ?? sentence(status);
}

function sentence(value: string): string {
    const words = value.replaceAll("_", " ").toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
}

/* ── readers ───────────────────────────────────────────────────────── */

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

const SCOPES: Record<string, Runtime["scope"]> = {
    SYSTEM: "system",
    ORGANIZATION: "org",
    PERSONAL: "personal",
};

/** Why a saved coding agent cannot take work. A provider key is a URL and a
 *  key — always reachable — so it reports nothing, and `READY` is nothing to
 *  report either. */
export function runtimeTrouble(harnessId: string, availability: string): string {
    if (!harnessId) return "";
    switch (availability) {
        case "OFFLINE":
            return "Computer offline";
        case "NOT_INSTALLED":
            return "Not installed";
        case "UNAVAILABLE":
        case "UNAVAILABLE_FOR_YOU":
            return "Unavailable";
        default:
            return "";
    }
}

export function readRuntime(raw: unknown): Runtime | null {
    const entry = (raw ?? {}) as {
        id?: string;
        name?: string;
        kind?: string;
        status?: string;
        scope?: string;
        harness_id?: string | null;
        availability_status?: string | null;
        default_model_name?: string | null;
        model_catalog?: unknown[];
        metadata?: Record<string, unknown> | null;
    };
    const id = asString(entry.id);
    if (!id) return null;

    const harnessId = asString(entry.harness_id);
    const models = (entry.model_catalog ?? []).map(readModel).filter((model): model is RuntimeModel => model !== null);
    const defaultModel = asString(entry.default_model_name);

    /* A saved profile keeps the harness it was made from in its metadata, so
       a row can wear the right logo without re-reading the computer. */
    const harness = asString(entry.metadata?.["harness_key"]);

    return {
        id,
        name: asString(entry.name) || id,
        kind: entry.kind === "HARNESS" ? "agent" : "key",
        harness,
        harnessId,
        /* A profile with no catalog still offers the one model it names. An
           empty list would draw "0 models" beside a runtime that works. */
        models: models.length > 0 || !defaultModel
            ? models
            : [{ name: defaultModel, label: shortModel(defaultModel) }],
        defaultModel,
        scope: SCOPES[asString(entry.scope)] ?? "org",
        archived: entry.status === "DISABLED",
        trouble: runtimeTrouble(harnessId, asString(entry.availability_status)),
    };
}

/** The models a coding agent advertises, out of its `model` config option.
 *  Config options are an open shape, so this reads defensively — a value is
 *  `value` or `id`, and anything without one is not a choice. */
export function agentModels(configOptions: unknown): RuntimeModel[] {
    const options = Array.isArray(configOptions) ? configOptions : [];
    const models: RuntimeModel[] = [];
    for (const raw of options) {
        const option = (raw ?? {}) as { category?: string; options?: unknown[] };
        if (option.category !== "model") continue;
        for (const rawItem of option.options ?? []) {
            const item = (rawItem ?? {}) as { value?: string; id?: string; name?: string };
            const value = asString(item.value) || asString(item.id);
            if (!value) continue;
            models.push({ name: value, label: asString(item.name) || shortModel(value) });
        }
    }
    return models;
}

export function readLocalAgent(raw: unknown): LocalAgent | null {
    const entry = (raw ?? {}) as {
        id?: string;
        harness_key?: string;
        display_name?: string;
        upstream_version?: string | null;
        health?: string;
        config_options?: unknown;
    };
    const id = asString(entry.id);
    if (!id) return null;
    const harness = asString(entry.harness_key);
    const health = agentHealth(asString(entry.health));
    return {
        id,
        harness,
        /* The catalogue name wins over the computer's own, so "Claude Code"
           does not arrive as "claude-code" on one machine and "Claude Code
           CLI" on another. */
        name: agentLabel(harness) || asString(entry.display_name) || harness || id,
        version: asString(entry.upstream_version),
        models: agentModels(entry.config_options),
        ready: health.ready,
        state: health.state,
        fix: health.fix,
    };
}

export function readComputer(raw: unknown): Computer | null {
    const entry = (raw ?? {}) as {
        id?: string;
        display_name?: string;
        status?: string;
        host_release?: string;
        last_seen_at?: string | null;
        created_at?: string;
    };
    const id = asString(entry.id);
    if (!id) return null;
    return {
        id,
        name: asString(entry.display_name) || "A computer",
        online: entry.status === "ONLINE",
        status: computerStatus(asString(entry.status)),
        release: asString(entry.host_release),
        lastSeen: asString(entry.last_seen_at),
        agents: [],
        pairedAt: asString(entry.created_at),
    };
}

/** How long a computer may plausibly still be finding its agents.
 *
 *  Nothing on the wire tells "found none" from "still looking" — a computer
 *  publishes its list once it has one — so it is inferred from how long ago
 *  it was paired. Saying "no agents here" while the first probe is still
 *  running is the one lie this page could tell repeatedly. */
export const LOOKING_WINDOW_MS = 60_000;

export function stillLooking(computer: Computer, now = Date.now()): boolean {
    if (computer.agents.length > 0) return false;
    if (!computer.online) return false;
    const paired = Date.parse(computer.pairedAt);
    return Number.isNaN(paired) ? false : now - paired < LOOKING_WINDOW_MS;
}

/* ── the choice ────────────────────────────────────────────────────── */

/** The model a choice will actually run on.
 *
 *  A stored choice routinely names a runtime and leaves the model open, and
 *  the backend fills that gap at dispatch: the runtime's own default, else the
 *  first model it offers. Resolving it the same way here is the difference
 *  between a page that says "sonnet" and a page that says "Default" about a
 *  perfectly well-defined model. */
export function chosenModel(runtime: Runtime | undefined, choice: Choice | null): string {
    if (choice?.model) return choice.model;
    if (!runtime) return "";
    return runtime.defaultModel || runtime.models[0]?.name || "";
}

/** How a choice reads on one line: the runtime, and the model when it adds
 *  anything. A coding agent that runs one model says its name once. */
export function describeChoice(runtimes: Runtime[], choice: Choice | null): string {
    if (!choice) return "";
    const runtime = runtimes.find((entry) => entry.id === choice.runtimeId);
    if (!runtime) return "";
    const model = chosenModel(runtime, choice);
    const label = model ? (runtime.models.find((entry) => entry.name === model)?.label ?? shortModel(model)) : "";
    return label && label !== runtime.name ? runtime.name + " · " + label : runtime.name;
}

export function readChoice(raw: unknown): Choice | null {
    const entry = (raw ?? {}) as { profile_id?: string; model_name?: string | null };
    const runtimeId = asString(entry.profile_id);
    if (!runtimeId) return null;
    return { runtimeId, model: asString(entry.model_name) };
}

export function sameChoice(a: Choice | null, b: Choice | null): boolean {
    if (!a || !b) return a === b;
    return a.runtimeId === b.runtimeId && a.model === b.model;
}
