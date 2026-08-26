import { HarnessKind } from 'lemma-sdk';
import type { AgentHostResponse } from 'lemma-sdk';
import type {
    AgentRuntimeConfig,
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
    AvailableModelInfo,
    RuntimeModelCatalogEntry,
} from 'lemma-sdk';
import { humanizeName } from '@/lib/utils/display-name';

// There is no longer a HarnessKind per coding tool — Codex, Claude Code and the
// rest are all HarnessKind.HARNESS, dispatched through a paired machine's Agent
// Host. So "is this a local coding agent rather than a plain model provider?"
// is a single comparison, and *which* agent it is comes from `harness_key`.
export function isLocalAgentKind(kind?: string | null): boolean {
    return kind === HarnessKind.HARNESS;
}

// Keyed by the `harness_key` Agent Host publishes for each adapter it ships
// (see desktop/agent-host/agent-adapters.lock.json), which is also what a runtime
// profile created from a harness records in its metadata.
export const HARNESS_LOGOS: Partial<Record<string, string>> = {
    'claude-code': '/harnesslogos/claudecode.png',
    codex: '/harnesslogos/codex.png',
    cursor: '/harnesslogos/cursor.png',
    opencode: '/harnesslogos/opencode.png',
};

/**
 * How long a computer may plausibly still be finding its coding agents.
 *
 * This was ten minutes, and the reasoning behind it was sound at the time: the
 * first pairing did not probe agents, it *installed* them — a pinned adapter
 * package per certified agent, fetched against an empty npm cache, ahead of
 * anything else. The list really was empty for minutes, and saying "No agents
 * published yet" after two seconds was a lie the page told constantly.
 *
 * Three things moved underneath it. Installing no longer happens on the pairing
 * path at all; it is warmed when the app opens. The adapters no longer drag
 * along vendored copies of the agents themselves, so the download is a
 * twelfth of what it was. And an adapter that genuinely is still installing now
 * reports itself as such instead of being indistinguishable from a missing one.
 *
 * So the honest window is the budget, not the worst case of a path that no
 * longer exists. A minute is two full budgets of patience.
 */
export const HARNESS_DISCOVERY_WINDOW_MS = 60_000;

/**
 * Whether an empty harness list means "still looking" rather than "found none".
 *
 * Nothing on the wire distinguishes the two — a host only publishes once it has
 * at least one harness — so this is inferred from how long the computer has
 * been paired. Anchored on `created_at`, because the expensive step is the
 * first install on a machine, not the re-probe on later launches.
 */
export const isDiscoveringHarnesses = (
    host: Pick<AgentHostResponse, 'status' | 'created_at'>,
    harnessCount: number,
): boolean => {
    if (harnessCount > 0) return false;
    if (host.status === 'REVOKED') return false;
    const pairedAt = Date.parse(host.created_at);
    if (Number.isNaN(pairedAt)) return false;
    return Date.now() - pairedAt < HARNESS_DISCOVERY_WINDOW_MS;
};

export function harnessLogo(harnessKey?: string | null): string | undefined {
    return harnessKey ? HARNESS_LOGOS[harnessKey] : undefined;
}

// A saved runtime profile keeps the harness it was created from in its
// metadata, so a profile row can still show the right coding-agent logo without
// re-fetching the host it belongs to.
export function profileHarnessKey(
    profile?: { metadata?: Record<string, unknown> | null } | null,
): string | null {
    const key = profile?.metadata?.harness_key;
    return typeof key === 'string' && key ? key : null;
}

export function runtimeKey(runtime: AgentRuntimeConfig): string {
    return `${runtime.profile_id}::${runtime.model_name ?? ''}`;
}

export type RuntimeModelOption = RuntimeModelCatalogEntry & {
    name: string;
};

export type CustomProviderKind = 'openai' | 'anthropic';

export const CUSTOM_PROVIDER_OPTIONS: Array<{
    kind: CustomProviderKind;
    title: string;
    subtitle: string;
    defaultBaseUrl: string;
}> = [
    {
        kind: 'openai',
        title: 'OpenAI-compatible',
        subtitle: 'Custom route and API key',
        defaultBaseUrl: '',
    },
    {
        kind: 'anthropic',
        title: 'Anthropic-compatible',
        subtitle: 'Claude-compatible route and key',
        defaultBaseUrl: 'https://api.anthropic.com',
    },
];

export function runtimeModels(profile?: AgentRuntimeProfileResponse): RuntimeModelOption[] {
    if (!profile) return [];
    const models = profile.model_catalog ?? [];
    if (models.length > 0) return models as RuntimeModelOption[];
    if (profile.default_model_name) {
        return [{
            name: profile.default_model_name,
            display_name: null,
            provider_model_name: profile.default_model_name,
            capabilities: [],
            default_model_settings: {},
            metadata: {},
        }];
    }
    return [];
}

export function findProfileByRuntime(catalog: AgentRuntimeProfileListResponse | undefined, runtime?: AgentRuntimeConfig | null) {
    if (!runtime) return undefined;
    return catalog?.items.find((profile) => profile.id === runtime.profile_id);
}

export function defaultAgentRuntimeFromProfile(
    profile?: AgentRuntimeProfileResponse | null,
): AgentRuntimeConfig | null {
    if (!profile) return null;
    return {
        profile_id: profile.id,
        model_name: profile.default_model_name ?? runtimeModels(profile)[0]?.name ?? null,
    };
}

// The model a runtime will actually run on. A stored runtime routinely pins a
// profile and leaves the model open: the catalog's own `default_runtime` is a
// bare `{ profile_id: 'system:lemma' }`, a pod that never set a default has no
// runtime at all, and an agent can inherit its profile's model. The backend
// fills that gap at dispatch — the profile's default model, else the first
// catalog entry (`_selected_model` in runtime_profile_service.py) — so resolve
// it the same way here instead of calling a well-defined model "Default".
export function resolveRuntimeModelName(
    runtime?: AgentRuntimeConfig | null,
    catalog?: AgentRuntimeProfileListResponse,
): string | null {
    if (!runtime) return null;
    if (runtime.model_name) return runtime.model_name;
    const profile = findProfileByRuntime(catalog, runtime);
    return profile?.default_model_name ?? runtimeModels(profile)[0]?.name ?? null;
}

// The same runtime with its model spelled out, for pickers and labels that read
// `model_name` directly. Returned unchanged when nothing can name the model —
// while the catalog is still loading, or when its profile has gone away.
export function hydrateRuntimeModel(
    runtime?: AgentRuntimeConfig | null,
    catalog?: AgentRuntimeProfileListResponse,
): AgentRuntimeConfig | null {
    if (!runtime) return null;
    if (runtime.model_name) return runtime;
    const modelName = resolveRuntimeModelName(runtime, catalog);
    return modelName ? { ...runtime, model_name: modelName } : runtime;
}

export function resolveDefaultAgentRuntime(
    catalog?: AgentRuntimeProfileListResponse,
    profileId?: string | null,
): AgentRuntimeConfig | null {
    const profile = profileId
        ? catalog?.items.find((item) => item.id === profileId)
        : undefined;
    return defaultAgentRuntimeFromProfile(profile)
        ?? hydrateRuntimeModel(catalog?.default_runtime, catalog);
}

export function formatAgentRuntime(
    runtime?: AgentRuntimeConfig | null,
    catalog?: AgentRuntimeProfileListResponse,
    { includeModel = true }: { includeModel?: boolean } = {},
): string {
    if (!runtime) return includeModel ? 'Default model' : 'Default Agent Runtime';
    const profile = findProfileByRuntime(catalog, runtime);
    // Resolve through this runtime's own profile — borrowing the catalog
    // default's model would name a model this profile may not even serve.
    const modelName = resolveRuntimeModelName(runtime, catalog);
    const prefix = profile?.name ?? (runtime.profile_id === catalog?.default_runtime?.profile_id ? 'Default Agent Runtime' : runtime.profile_id);
    return includeModel && modelName ? `${prefix} · ${shortModelName(modelName)}` : prefix;
}

// A short advisory drawn from a model's catalog metadata — e.g. a credits
// requirement or a non-standard context window. Returns null for plain
// standard-context models so the picker stays uncluttered.
export function modelAdvisory(model: Pick<RuntimeModelCatalogEntry, 'metadata'>): string | null {
    const metadata = (model.metadata ?? {}) as Record<string, unknown>;
    if (metadata.requires_credits) return 'Requires usage credits';
    const note = typeof metadata.note === 'string' ? metadata.note.trim() : '';
    if (note) return note;
    const contextWindow = typeof metadata.context_window === 'string'
        ? metadata.context_window.trim()
        : '';
    if (contextWindow && contextWindow.toLowerCase() !== 'standard') {
        return `${contextWindow} context`;
    }
    return null;
}

export function shortModelName(modelName: string): string {
    const normalized = modelName.replace(/\/$/, '');
    const markerMatch = normalized.match(/\/(?:models|routers)\/([^/]+)$/);
    if (markerMatch?.[1]) return markerMatch[1];
    return normalized.split('/').filter(Boolean).at(-1) || normalized;
}

/**
 * The model name as a person reads it: the short name, humanised.
 *
 * Kept separate from `shortModelName` on purpose — that one also feeds the
 * picker's search haystack, which matches against the raw model name.
 * Humanising in there would break it.
 */
export function humanizeModelName(modelName: string): string {
    return humanizeName(shortModelName(modelName));
}

// A paired Agent Host reports each harness's health from its own probe, so
// every non-READY state is fixed on that computer rather than here. Pair the
// label with the fix so an unusable harness never reads as an opaque code.
const AGENT_HOST_HARNESS_HEALTH: Record<string, { label: string; detail: string }> = {
    READY: { label: 'Ready', detail: 'Accepting runs.' },
    AUTH_REQUIRED: {
        label: 'Sign-in needed',
        detail: 'Sign in to this agent on that computer, then let Agent Host re-probe.',
    },
    UNSUPPORTED_VERSION: {
        label: 'Version unsupported',
        detail: 'Update this agent on that computer to a release Agent Host supports.',
    },
    CONFIG_INVALID: {
        label: 'Configuration invalid',
        detail: "This agent's settings on that computer were rejected. Fix them, then re-probe.",
    },
    PROBE_FAILED: {
        label: 'Probe failed',
        detail: 'Agent Host could not start this agent. Check the Agent Host log on that computer.',
    },
    // Covers the whole not-ready-yet window, because the row cannot see where in
    // it we are. Fetching the adapter takes about three seconds; the rest is the
    // probe, which starts the agent and opens a session with it and is most of
    // the wait. Saying "installing" for the twenty-odd seconds after the install
    // has finished was a lie the user could time.
    INSTALLING: {
        label: 'Setting up',
        detail: 'Starting this agent to see what it offers. Usually under a minute.',
    },
    DISABLED: { label: 'Disabled', detail: 'Turned off in the Agent Host configuration on that computer.' },
};

// The sentence the Agent Host writes when a coding agent on this Mac is
// installed but signed out (`authentication_hint`, desktop/agent-host/src/runtime.rs).
// It reaches the user through two different doors — a harness row's
// `stale_reason` and a failed run's error — and only one of them used to offer
// anything to press.
const LOCAL_AGENT_SIGN_IN_MARKER = 'installed on this computer but not signed in';

/**
 * Whether this failure is a local coding agent that just needs signing in.
 *
 * Matters because "send the message again" does not fix it: a signed-out
 * harness is published AUTH_REQUIRED and admission refuses every run against it
 * until the host re-probes, which is otherwise up to fifteen minutes away. The
 * fix is to ask the host to look again — so the failure carries that action
 * rather than advice that cannot work yet.
 *
 * Deliberately a substring test on one stable clause, not a parse: the message
 * is written for a person and its wording will move.
 */
export function isLocalAgentSignInFailure(detail: string | null | undefined): boolean {
    return typeof detail === 'string' && detail.includes(LOCAL_AGENT_SIGN_IN_MARKER);
}

export function agentHostHarnessHealth(health: string): { label: string; detail: string; ready: boolean } {
    const known = AGENT_HOST_HARNESS_HEALTH[health];
    if (known) return { ...known, ready: health === 'READY' };
    return {
        label: humanizeAgentHostState(health),
        detail: 'That computer reported a state this version of Lemma does not recognize yet.',
        ready: false,
    };
}

/**
 * One harness, described once, for whichever layout is drawing it.
 *
 * `HarnessRow` draws a card in onboarding; the models ledger draws a bare row.
 * They disagreed about how much to say once already — which is why the row was
 * extracted in the first place — so what a harness *is* lives here, and only
 * where the pieces land is the layout's business.
 */
export function describeHarness(
    harness: {
        harness_key: string;
        upstream_version?: string | null;
        health: string;
        config_options?: Array<{ category: string; options?: Array<Record<string, unknown>> }> | null;
    },
    { hostOnline = true }: { hostOnline?: boolean } = {},
): {
    logo: string | undefined;
    /** The agent's version and model count — the two facts a reader can act on. */
    facts: string[];
    statusLabel: string;
    usable: boolean;
    /** What to say when the row cannot take work and the status alone won't explain it. */
    blockedReason: string | null;
} {
    const health = agentHostHarnessHealth(harness.health);
    const modelCount = agentHostHarnessModelCount(harness.config_options ?? []);
    const usable = health.ready && hostOnline;
    return {
        logo: harnessLogo(harness.harness_key),
        facts: [
            harness.upstream_version ? `agent ${harness.upstream_version}` : null,
            modelCount ? `${modelCount} model${modelCount === 1 ? '' : 's'}` : null,
        ].filter((fact): fact is string => fact !== null),
        // Reachability decides first: a healthy agent on a sleeping laptop is
        // not "Ready", whatever the harness itself last reported.
        statusLabel: hostOnline ? health.label : 'Computer offline',
        usable,
        // An unreachable computer is stated by the computer's own row; repeating
        // it under every agent it owns is the same sentence three times.
        blockedReason: usable || !hostOnline ? null : health.detail,
    };
}

const AGENT_HOST_STATUS_LABELS: Record<string, string> = {
    ONLINE: 'Online',
    OFFLINE: 'Offline',
    DRAINING: 'Draining',
    UPGRADE_REQUIRED: 'Upgrade required',
    REVOKED: 'Revoked',
};

export function agentHostStatusLabel(status: string): string {
    return AGENT_HOST_STATUS_LABELS[status] ?? humanizeAgentHostState(status);
}

function humanizeAgentHostState(value: string): string {
    const words = value.replaceAll('_', ' ').toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
}

// Model choices a harness advertises, used only to tell the user how much this
// harness brings. Config options are an open shape, so count defensively.
export function agentHostHarnessModelCount(
    options: Array<{ category: string; options?: Array<Record<string, unknown>> }>,
): number {
    return options.reduce(
        (total, option) => total + (option.category === 'model' ? (option.options?.length ?? 0) : 0),
        0,
    );
}

// Why a harness-backed profile can't take work right now. Provider profiles
// (a base URL and a key) are always reachable, so they have nothing to report.
export function runtimeAvailabilityLabel(profile: AgentRuntimeProfileResponse): string | null {
    if (!profile.harness_id) return null;
    switch (profile.availability_status) {
        case 'READY':
            return null;
        case 'OFFLINE':
            return 'Offline';
        case 'NOT_INSTALLED':
            return 'Not installed';
        case 'UNAVAILABLE_FOR_YOU':
            return 'Unavailable';
        case 'UNAVAILABLE':
            return 'Unavailable';
        default:
            return null;
    }
}

/**
 * Whether this profile's model and config selections can be changed right now.
 *
 * Mirrors the backend rule rather than guessing at it: `update_agent_host_profile`
 * contacts the paired computer only for an edit that touches `default_model_name`
 * or `config_selections`, because those are validated against what the harness
 * advertises at that moment. A rename never leaves the database and works while
 * the machine is asleep.
 *
 * Deliberately false only on a *positive* signal. The backend leaves
 * `availability_status` null at the two call sites built without a host
 * repository, and treating "unknown" as offline would disable a control the user
 * can in fact save.
 */
export function canConfigureHarnessProfile(profile: {
    availability_status?: string | null;
}): boolean {
    return profile.availability_status == null || profile.availability_status === 'READY';
}

// A profile the workspace has retired. It keeps working for history and can be
// restored, but it is out of the catalog and cannot be picked for new runs.
export function isArchivedProfile(profile: { status?: string | null }): boolean {
    return profile.status === 'DISABLED';
}

export type HarnessConfigControl = {
    id: string;
    /** The key the backend expects in `config_selections`. */
    selectionKey: string;
    label: string;
    description: string | null;
    /** What that computer is set to now, when it names one of the choices. */
    currentValue: string | null;
    choices: Array<{ value: string; label: string }>;
};

// Options whose value decides how much the agent may do unattended, and the
// values Agent Host refuses for them. Mirrors `is_policy_bearing_option` /
// `is_disallowed_policy_value` (desktop/agent-host/src/acp.rs) and the same pair in the
// backend domain. Harnesses *do* enumerate these — Claude Code lists
// `bypassPermissions` among its permission modes — and the host rejects them
// anyway at session setup, so offering one here would be a dead choice.
// Settings Lemma owns, so the dialog must not offer them per profile.
//
// `mode` is the agent's approval and sandboxing preset. Approvals are the
// platform's job — a run asks, Lemma surfaces it, a human answers — and that
// must behave identically whichever harness is executing. Letting each profile
// pick a preset makes the same question answerable in several different ways.
//
// `collaboration_mode` decides how the agent carries state across turns. Lemma
// maps one conversation to one session already, so this is decided by the
// conversation, not by the profile.
//
// The harness keeps applying its own safe default for both, which is what
// "the same as the Lemma server default harness" means in practice.
export const PLATFORM_OWNED_OPTION_CATEGORIES = ['mode', 'collaboration_mode'];

const POLICY_OPTION_MARKERS = ['mode', 'permission', 'approval', 'sandbox'];
const DISALLOWED_POLICY_VALUES = new Set([
    'bypasspermissions',
    'agentfullaccess',
    'fullaccess',
    'acceptedits',
    'yolo',
    'auto',
]);

function isPolicyBearing(selectionKey: string, category: string): boolean {
    const identity = `${selectionKey} ${category}`.toLowerCase();
    return POLICY_OPTION_MARKERS.some((marker) => identity.includes(marker));
}

function isDisallowedPolicyValue(value: string): boolean {
    return DISALLOWED_POLICY_VALUES.has(value.replace(/[^a-z0-9]/gi, '').toLowerCase());
}

/**
 * The harness config options this UI can safely offer a control for.
 *
 * Mirrors `validate_agent_host_selections` in the backend domain: a selection is
 * keyed by the option's `id` or its `category`, `model` is rejected outright
 * (models are chosen through `default_model_name`), and an allowed value is
 * `item.value ?? item.id`.
 *
 * Options that enumerate no values are dropped rather than rendered as a text
 * box, and escalating values are dropped from the ones that do — either would
 * let a selection save cleanly and then fail on the user's first run.
 */
export function harnessConfigControls(
    configOptions?: Array<{
        id?: string | null;
        name?: string | null;
        category?: string | null;
        description?: string | null;
        current_value?: unknown;
        options?: Array<Record<string, unknown>> | null;
    }> | null,
): HarnessConfigControl[] {
    const controls: HarnessConfigControl[] = [];
    for (const option of configOptions ?? []) {
        const category = typeof option.category === 'string' ? option.category : '';
        // `model` is chosen through default_model_name; the rest are Lemma's.
        if (category === 'model') continue;
        if (PLATFORM_OWNED_OPTION_CATEGORIES.includes(category)) continue;
        const selectionKey = (typeof option.id === 'string' && option.id) || category;
        if (!selectionKey) continue;

        const policyBearing = isPolicyBearing(selectionKey, category);
        const choices: Array<{ value: string; label: string }> = [];
        for (const item of option.options ?? []) {
            const value = item.value ?? item.id;
            if (typeof value !== 'string' || !value) continue;
            if (policyBearing && isDisallowedPolicyValue(value)) continue;
            const label = typeof item.name === 'string' && item.name ? item.name : value;
            choices.push({ value, label });
        }
        if (!choices.length) continue;

        const currentValue = typeof option.current_value === 'string' ? option.current_value : null;
        controls.push({
            id: selectionKey,
            selectionKey,
            label: (typeof option.name === 'string' && option.name) || selectionKey,
            description: typeof option.description === 'string' && option.description
                ? option.description
                : null,
            currentValue: choices.some((choice) => choice.value === currentValue) ? currentValue : null,
            choices,
        });
    }
    return controls;
}

// Radix Select refuses value="", so "let the agent decide" needs a real token.
// The composer solves the same problem with POD_DEFAULT_AGENT_VALUE.
export const HARNESS_DEFAULT_VALUE = '__harness_default__';

export type HarnessProfileFields = {
    name: string;
    description: string;
    /** A model name, or HARNESS_DEFAULT_VALUE for "let the agent choose". */
    defaultModel: string;
    selections: Record<string, string>;
};

/**
 * The PATCH body for a harness profile: only what the user actually changed.
 *
 * This is not just tidiness. The backend contacts the paired computer *only*
 * when an edit touches `default_model_name` or `config_selections`, precisely so
 * that a rename works while that machine is asleep
 * (`touches_configuration` in runtime_profile_editor.py). Sending those fields
 * unconditionally would require the machine to be online and READY to rename a
 * coding agent, and fail with "not available" when it is not.
 */
export function harnessProfileChanges(
    original: HarnessProfileFields,
    next: HarnessProfileFields,
): Record<string, unknown> {
    const changes: Record<string, unknown> = {};

    const name = next.name.trim();
    if (name !== original.name.trim()) changes.name = name;

    const description = next.description.trim();
    if (description !== original.description.trim()) {
        changes.description = description || null;
    }

    if (next.defaultModel !== original.defaultModel) {
        changes.default_model_name =
            next.defaultModel === HARNESS_DEFAULT_VALUE ? null : next.defaultModel;
    }

    const nextSelections = liveConfigSelections(next.selections);
    if (!sameSelections(nextSelections, liveConfigSelections(original.selections))) {
        changes.config_selections = nextSelections;
    }

    return changes;
}

/** Drop the "use this computer's setting" sentinel and any empty choice. */
export function liveConfigSelections(
    selections: Record<string, string>,
): Record<string, string> {
    return Object.fromEntries(
        Object.entries(selections).filter(
            ([, value]) => value && value !== HARNESS_DEFAULT_VALUE,
        ),
    );
}

function sameSelections(a: Record<string, string>, b: Record<string, string>): boolean {
    const keys = Object.keys(a);
    if (keys.length !== Object.keys(b).length) return false;
    return keys.every((key) => a[key] === b[key]);
}

// Flatten the runtime-profile catalog into the flat, plain-language model list
// the ModelPicker consumes. Every pickable model across every saved profile
// (Lemma built-in, BYO providers, coding agents) becomes one row, tagged with
// its profile so the picker can group by provider. Provider/host *creation* is
// intentionally not represented here — that lives in the manage surface.
export function runtimeCatalogToModelOptions(
    catalog?: AgentRuntimeProfileListResponse,
): AvailableModelInfo[] {
    if (!catalog?.items?.length) return [];
    const options: AvailableModelInfo[] = [];
    for (const profile of catalog.items) {
        for (const model of runtimeModels(profile)) {
            options.push({
                id: model.name as AvailableModelInfo['id'],
                name: model.display_name ?? model.name,
                runtime: { profile_id: profile.id, model_name: model.name },
                profile,
                profile_id: profile.id,
                harness_kind: profile.derived_harness_kind ?? undefined,
                description: modelAdvisory(model),
            });
        }
    }
    return options;
}

export function splitModelNames(value: string): string[] {
    return value
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean);
}

// Agent Host used to have a second install channel: a CLI that downloaded the
// binary from a release and registered it as an OS service, which is what the
// pairing-code path here was for. Desktop supervises the only copy now, so a
// machine connects by running Desktop and signing in — there is no code to
// carry, and nothing that could consume one.
