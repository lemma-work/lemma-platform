import { HarnessKind } from 'lemma-sdk';
import type {
    AgentRuntimeConfig,
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
    AvailableModelInfo,
    RuntimeModelCatalogEntry,
} from 'lemma-sdk';

// There is no longer a HarnessKind per coding tool — Codex, Claude Code and the
// rest are all HarnessKind.HARNESS, dispatched through a paired machine's Agent
// Host. So "is this a local coding agent rather than a plain model provider?"
// is a single comparison, and *which* agent it is comes from `harness_key`.
export function isLocalAgentKind(kind?: string | null): boolean {
    return kind === HarnessKind.HARNESS;
}

// Keyed by the `harness_key` Agent Host publishes for each adapter it ships
// (see agent-host/agent-adapters.lock.json), which is also what a runtime
// profile created from a harness records in its metadata.
export const HARNESS_LOGOS: Partial<Record<string, string>> = {
    'claude-code': '/harnesslogos/claudecode.png',
    codex: '/harnesslogos/codex.png',
    cursor: '/harnesslogos/cursor.png',
    opencode: '/harnesslogos/opencode.png',
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

export function resolveDefaultAgentRuntime(
    catalog?: AgentRuntimeProfileListResponse,
    profileId?: string | null,
): AgentRuntimeConfig | null {
    const profile = profileId
        ? catalog?.items.find((item) => item.id === profileId)
        : undefined;
    return defaultAgentRuntimeFromProfile(profile) ?? catalog?.default_runtime ?? null;
}

export function formatAgentRuntime(
    runtime?: AgentRuntimeConfig | null,
    catalog?: AgentRuntimeProfileListResponse,
    { includeModel = true }: { includeModel?: boolean } = {},
): string {
    if (!runtime) return includeModel ? 'Default model' : 'Default Agent Runtime';
    const profile = findProfileByRuntime(catalog, runtime);
    const modelName = runtime.model_name ?? catalog?.default_runtime?.model_name ?? null;
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

export function modelPathHint(modelName: string): string | null {
    const shortName = shortModelName(modelName);
    if (shortName === modelName) return null;
    return modelName.replace(new RegExp(`/?${escapeRegExp(shortName)}$`), '').replace(/\/$/, '') || modelName;
}

export function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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
    INSTALLING: { label: 'Installing', detail: 'Agent Host is still installing the adapter.' },
    DISABLED: { label: 'Disabled', detail: 'Turned off in the Agent Host configuration on that computer.' },
};

export function agentHostHarnessHealth(health: string): { label: string; detail: string; ready: boolean } {
    const known = AGENT_HOST_HARNESS_HEALTH[health];
    if (known) return { ...known, ready: health === 'READY' };
    return {
        label: humanizeAgentHostState(health),
        detail: 'That computer reported a state this version of Lemma does not recognize yet.',
        ready: false,
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
