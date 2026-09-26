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
    /** Empty for a coding agent means *unpinned*: the agent runs whatever it
     *  is set to on that computer, and dispatch sends no model at all. */
    defaultModel: string;
    /** A coding agent's saved option choices — effort, permission mode —
     *  keyed as the harness published them. Empty for a provider key. */
    selections: Record<string, string>;
    scope: "system" | "org" | "personal";
    /** Retired: out of the picker, still readable, restorable. */
    archived: boolean;
    /** Why it cannot take work right now, in a person's words. Empty when it
     *  can. A provider key is always reachable, so it never fills this in. */
    trouble: string;
    /** A provider key's route, when the API says. Empty for a coding agent.
     *  Read so the desktop app can tell a model server on this computer is
     *  already in the list before suggesting it again. */
    baseUrl?: string;
}

/** One coding agent as a computer reports it, before anyone has added it. */
export interface LocalAgent {
    /** The harness id — what a profile is created from. */
    id: string;
    harness: string;
    name: string;
    version: string;
    models: RuntimeModel[];
    /** The model it runs when nobody pins one: the harness's own
     *  `current_value`. Empty when it did not say. */
    defaultModel: string;
    /** Everything else it lets a person choose, model aside. */
    options: AgentOption[];
    /** READY means this computer would take a run right now. */
    ready: boolean;
    /** The computer's own word for the state — `AUTH_REQUIRED` and so on —
     *  for the fix that depends on which computer is reading it. */
    health: string;
    /** The state, said the way a person would say it. */
    state: string;
    /** What to do about it, when there is something to do. */
    fix: string;
}

/** One setting a coding agent publishes besides its model.
 *
 *  Read generically, because the harnesses name them differently — Codex's
 *  effort is `reasoning_effort` with low, medium and high, OpenCode's is
 *  `effort` with low, high and max — and only the ACP `category` is shared. A
 *  permission mode is one of these too. Every value is offered as published:
 *  the host has already removed the ones Lemma refuses (it marks those
 *  options `metadata.policy`), and repeating that rule here is how the
 *  frontend and the host came to disagree about plan mode. */
export interface AgentOption {
    /** What a selection is stored under: the option's id, else its category. */
    key: string;
    kind: "effort" | "mode" | "other";
    label: string;
    description: string;
    /** What that computer is set to now, when it names one of the choices. */
    current: string;
    choices: { value: string; label: string }[];
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

/** What testing a saved provider found. `message` is always the backend's
 *  own fixed sentence, never the provider's text; `models` is `null` when
 *  the provider listed none it would share. */
export interface RuntimeTest {
    ok: boolean;
    message: string;
    models: string[] | null;
}

/* ── the four agents Lemma drives ──────────────────────────────────── */

/** Keyed by the `harness_key` a paired computer publishes. A key that is not
 *  here still draws a row — it simply wears the generic mark and its own
 *  name, which is the honest answer for an agent this app has not been taught
 *  about yet.
 *
 *  `signIn` and `update` are the agents' own commands, typed in a terminal on
 *  the computer the agent is on. Lemma cannot run either for anyone: signing
 *  in is the person's own account, and updating is their own install. */
const AGENTS: Record<string, { label: string; logo: string; signIn: string; update: string }> = {
    "claude-code": {
        label: "Claude Code",
        logo: "/agent-logos/claudecode.png",
        signIn: "claude login",
        update: "claude update",
    },
    codex: {
        label: "Codex",
        logo: "/agent-logos/codex.png",
        signIn: "codex login",
        update: "npm install -g @openai/codex@latest",
    },
    cursor: {
        label: "Cursor",
        logo: "/agent-logos/cursor.png",
        signIn: "cursor-agent login",
        update: "cursor-agent update",
    },
    opencode: {
        label: "OpenCode",
        logo: "/agent-logos/opencode.png",
        signIn: "opencode auth login",
        update: "opencode upgrade",
    },
};

export function agentLabel(harness: string): string {
    return AGENTS[harness]?.label ?? "";
}

export function agentLogo(harness: string): string {
    return AGENTS[harness]?.logo ?? "";
}

/** The command that signs in to an agent, when this app knows it. */
export function agentSignInCommand(harness: string): string {
    return AGENTS[harness]?.signIn ?? "";
}

/** The command that updates an agent, when this app knows it. */
export function agentUpdateCommand(harness: string): string {
    return AGENTS[harness]?.update ?? "";
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
    DISABLED: { state: "Turned off", fix: "Turned off in the Lemma app on that computer. Turn it back on there." },
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

/** What to do about an agent that is not ready, said for where it is.
 *
 *  `here` names the computer this app runs on — "this Mac" — when the agent
 *  is on it, and is null for any other. Here the reader can act at once: the
 *  command to type, and the button that makes this computer look again
 *  instead of on its own quarter-hour cycle, so the words name both. On any
 *  other computer the fix is still over there, and the generic sentence is
 *  the honest one. */
export function agentFix(agent: Pick<LocalAgent, "harness" | "health" | "fix">, here: string | null): string {
    if (!here) return agent.fix;
    const signIn = agentSignInCommand(agent.harness);
    const update = agentUpdateCommand(agent.harness);
    switch (agent.health) {
        case "AUTH_REQUIRED":
            return signIn
                ? "Run `" + signIn + "` in Terminal, then press Check again."
                : "Sign in to this agent on " + here + ", then press Check again.";
        case "UNSUPPORTED_VERSION":
            return update
                ? "Run `" + update + "` in Terminal to update it, then press Check again."
                : "Update this agent on " + here + " to a release Lemma supports, then press Check again.";
        case "CONFIG_INVALID":
            return "Its settings on " + here + " were rejected. Fix them, then press Check again.";
        case "PROBE_FAILED":
            return "Lemma could not start it on " + here + ". Open the log to see why, then press Check again.";
        case "DISABLED":
            return "Turned off in the Lemma app on " + here + ".";
        default:
            return agent.fix;
    }
}

const HOST_STATUS: Record<string, string> = {
    ONLINE: "Online",
    OFFLINE: "Offline",
    DRAINING: "Finishing up",
    /* The workspace speaks a newer protocol than that computer's Lemma app.
       Nothing on this side can fix it; the app over there updating does. */
    UPGRADE_REQUIRED: "Update needed",
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
        /* The backend says UNAVAILABLE for two things: the computer was
           removed, or the agent on it is not ready (starting, misconfigured,
           turned off). Both mean "fix it on that computer", so that is what
           it says. */
        case "UNAVAILABLE":
            return "Not ready on its computer";
        case "UNAVAILABLE_FOR_YOU":
            return "Not shared with you";
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
        config?: Record<string, unknown> | null;
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
        selections: readSelections(entry.config?.["config_selections"]),
        scope: SCOPES[asString(entry.scope)] ?? "org",
        archived: entry.status === "DISABLED",
        trouble: runtimeTrouble(harnessId, asString(entry.availability_status)),
        baseUrl: asString(entry.config?.["base_url"]),
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

/** Saved selections, strings only: a selection is one of the values a harness
 *  published, and those are strings. */
function readSelections(raw: unknown): Record<string, string> {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    const selections: Record<string, string> = {};
    for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
        if (typeof value === "string" && value) selections[key] = value;
    }
    return selections;
}

/** The model an agent runs unpinned, off its `model` option. */
export function agentDefaultModel(configOptions: unknown): string {
    const options = Array.isArray(configOptions) ? configOptions : [];
    for (const raw of options) {
        const option = (raw ?? {}) as { category?: string; current_value?: unknown };
        if (option.category === "model") return asString(option.current_value);
    }
    return "";
}

const OPTION_KIND: Record<string, AgentOption["kind"]> = {
    thought_level: "effort",
    mode: "mode",
    collaboration_mode: "mode",
};
const KIND_ORDER: AgentOption["kind"][] = ["effort", "mode", "other"];

/** Every option an agent publishes besides its model, as choices a person can
 *  make. Keyed the way `validate_agent_host_selections` reads a selection —
 *  id first, then category — and an option that enumerates no values is left
 *  out rather than drawn as a text box nothing would accept. */
export function agentOptions(configOptions: unknown): AgentOption[] {
    const options = Array.isArray(configOptions) ? configOptions : [];
    const read: AgentOption[] = [];
    for (const raw of options) {
        const option = (raw ?? {}) as {
            id?: unknown;
            name?: unknown;
            category?: unknown;
            description?: unknown;
            current_value?: unknown;
            options?: unknown;
            metadata?: { policy?: unknown } | null;
        };
        const category = asString(option.category);
        if (category === "model") continue;
        const key = asString(option.id) || category;
        if (!key) continue;
        const choices: AgentOption["choices"] = [];
        for (const rawItem of Array.isArray(option.options) ? option.options : []) {
            const item = (rawItem ?? {}) as { value?: unknown; id?: unknown; name?: unknown };
            const value = asString(item.value) || asString(item.id);
            if (value) choices.push({ value, label: asString(item.name) || value });
        }
        if (!choices.length) continue;
        const current = asString(option.current_value);
        read.push({
            key,
            kind: OPTION_KIND[category] ?? (option.metadata?.policy === true ? "mode" : "other"),
            label: asString(option.name) || sentence(key),
            description: asString(option.description),
            current: choices.some((choice) => choice.value === current) ? current : "",
            choices,
        });
    }
    return read.sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));
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
        defaultModel: agentDefaultModel(entry.config_options),
        options: agentOptions(entry.config_options),
        ready: health.ready,
        health: asString(entry.health),
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

/** The model a choice will actually run on, or empty for "the agent's own".
 *
 *  A stored choice routinely names a runtime and leaves the model open, and
 *  the backend fills that gap at dispatch. For a provider key that is the
 *  runtime's own default, else the first model it offers, and resolving it
 *  the same way here is the difference between a page that says "sonnet" and
 *  one that says "Default" about a perfectly well-defined model.
 *
 *  A coding agent is the exception. Unpinned, dispatch sends no model and the
 *  agent runs whatever it is set to on that computer; a pin to a model it no
 *  longer offers falls back the same way. Naming the first model of its list
 *  would name a model that is not running. */
export function chosenModel(runtime: Runtime | undefined, choice: Choice | null): string {
    if (runtime?.kind === "agent") {
        const offered = (name: string) => Boolean(name) && runtime.models.some((model) => model.name === name);
        if (choice?.model && offered(choice.model)) return choice.model;
        return offered(runtime.defaultModel) ? runtime.defaultModel : "";
    }
    if (choice?.model) return choice.model;
    if (!runtime) return "";
    return runtime.defaultModel || runtime.models[0]?.name || "";
}

/** What an unpinned coding agent is called, with the model it runs when the
 *  computer has said. */
export function agentDefaultLabel(current = ""): string {
    return current ? "Agent default (" + shortModel(current) + ")" : "Agent default";
}

/** How a choice reads on one line: the runtime, and the model when it adds
 *  anything. A coding agent that runs one model says its name once. */
export function describeChoice(runtimes: Runtime[], choice: Choice | null): string {
    if (!choice) return "";
    const runtime = runtimes.find((entry) => entry.id === choice.runtimeId);
    if (!runtime) return "";
    const model = chosenModel(runtime, choice);
    const label = model
        ? (runtime.models.find((entry) => entry.name === model)?.label ?? shortModel(model))
        : runtime.kind === "agent"
          ? agentDefaultLabel()
          : "";
    return label && label !== runtime.name ? runtime.name + " · " + label : runtime.name;
}

/** A coding agent's settings as a form holds them: a model name or empty for
 *  "the agent's own", and a value per option or none for "as that computer
 *  has it". */
export interface AgentSettings {
    model: string;
    selections: Record<string, string>;
}

/** The PATCH body for a coding agent's settings: only what changed.
 *
 *  Not tidiness. The backend asks the paired computer to validate an edit
 *  only when it touches `default_model_name` or `config_selections`
 *  (`touches_configuration` in `runtime_profile_editor.py`), so sending them
 *  unchanged would make an unrelated save fail whenever that machine is
 *  asleep. Selections replace wholesale, so a change sends the whole map. */
export function agentSettingsChanges(
    before: AgentSettings,
    after: AgentSettings,
): { default_model_name?: string | null; config_selections?: Record<string, string> } {
    const changes: { default_model_name?: string | null; config_selections?: Record<string, string> } = {};
    if (after.model !== before.model) changes.default_model_name = after.model || null;
    const live = (selections: Record<string, string>) =>
        Object.fromEntries(Object.entries(selections).filter(([, value]) => Boolean(value)));
    const was = live(before.selections);
    const now = live(after.selections);
    const same =
        Object.keys(was).length === Object.keys(now).length && Object.keys(now).every((key) => was[key] === now[key]);
    if (!same) changes.config_selections = now;
    return changes;
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
