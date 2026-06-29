import { HarnessKind } from 'lemma-sdk';
import type {
    AgentHarnessListResponse,
    AgentHarnessInfo,
    AgentRuntimeConfig,
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
    AvailableModelInfo,
    RuntimeModelCatalogEntry,
} from 'lemma-sdk';

export const DEFAULT_VALUE = '__default_runtime__';
export const HARNESS_LOGOS: Partial<Record<string, string>> = {
    ANTIGRAVITY: '/harnesslogos/antigravity.png',
    CLAUDE_CODE: '/harnesslogos/claudecode.png',
    CODEX: '/harnesslogos/codex.png',
    OPENCODE: '/harnesslogos/opencode.png',
};
export const LOCAL_RUNTIME_SETUP_COMMANDS = ['lemma auth login', 'lemma daemon start --background'];
export const LOCAL_RUNTIME_SETUP_OPTIONS: Array<{
    harnessKind: HarnessKind;
    title: string;
}> = [
    { harnessKind: HarnessKind.CODEX, title: 'Codex' },
    { harnessKind: HarnessKind.CLAUDE_CODE, title: 'Claude Code' },
    { harnessKind: HarnessKind.OPENCODE, title: 'OpenCode' },
];

export function runtimeKey(runtime: AgentRuntimeConfig): string {
    return `${runtime.profile_id}::${runtime.model_name ?? ''}`;
}

export type RuntimeModelOption = RuntimeModelCatalogEntry & {
    name: string;
};

export type AgentRuntimeSelectionMode = 'runtime' | 'model';
export type AvailableHarnessOption = Omit<AgentHarnessInfo, 'models'> & {
    models: RuntimeModelOption[];
};
export type CustomProviderKind = 'openai' | 'anthropic';
export type LocalRuntimeSetupOption = {
    harnessKind: HarnessKind;
    title: string;
    statusLabel: string;
    daemonDisplayName?: string | null;
};

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

export function firstRuntime(catalog?: AgentRuntimeProfileListResponse): AgentRuntimeConfig | null {
    if (catalog?.default_runtime) return catalog.default_runtime;
    return null;
}

export function availableHarnessModels(
    profile: AgentRuntimeProfileResponse | undefined,
    availableHarnesses?: AgentHarnessListResponse,
): RuntimeModelOption[] {
    if (!profile) return [];
    const harness = availableHarnesses?.items.find((item) =>
        item.harness_kind === profile.derived_harness_kind && isHarnessAvailable(item)
    );
    return (harness?.models ?? []).map((modelName) => ({
        name: modelName,
        display_name: null,
        provider_model_name: modelName,
        capabilities: [],
        default_model_settings: {},
        metadata: {},
    }));
}

export function runtimeModels(
    profile?: AgentRuntimeProfileResponse,
    availableHarnesses?: AgentHarnessListResponse,
): RuntimeModelOption[] {
    if (!profile) return [];
    const models = profile.model_catalog ?? [];
    if (models.length > 0) return models as RuntimeModelOption[];
    const harnessModels = availableHarnessModels(profile, availableHarnesses);
    if (harnessModels.length > 0) return harnessModels;
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
    availableHarnesses?: AgentHarnessListResponse,
): AgentRuntimeConfig | null {
    if (!profile) return null;
    return {
        profile_id: profile.id,
        model_name: profile.default_model_name ?? runtimeModels(profile, availableHarnesses)[0]?.name ?? null,
    };
}

export function resolveDefaultAgentRuntime(
    catalog?: AgentRuntimeProfileListResponse,
    profileId?: string | null,
    availableHarnesses?: AgentHarnessListResponse,
): AgentRuntimeConfig | null {
    const profile = profileId
        ? catalog?.items.find((item) => item.id === profileId)
        : undefined;
    return defaultAgentRuntimeFromProfile(profile, availableHarnesses) ?? catalog?.default_runtime ?? null;
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

export function harnessLogo(harnessKind?: HarnessKind): string | undefined {
    return harnessKind ? HARNESS_LOGOS[harnessKind] : undefined;
}

export function harnessModelOptions(harness: AgentHarnessInfo): RuntimeModelOption[] {
    // Prefer the structured catalog (nice display names + context metadata);
    // fall back to the flat model aliases for older daemons.
    if (harness.model_catalog && harness.model_catalog.length > 0) {
        return harness.model_catalog as RuntimeModelOption[];
    }
    return (harness.models ?? []).map((modelName) => ({
        name: modelName,
        display_name: null,
        provider_model_name: modelName,
        capabilities: [],
        default_model_settings: {},
        metadata: {},
    }));
}

export function isHarnessAvailable(harness: AgentHarnessInfo): boolean {
    return harness.available !== false && harness.availability_status !== 'NOT_INSTALLED';
}

export function availableHarnessStatusLabel(harness: AgentHarnessInfo): string | null {
    if (!isHarnessAvailable(harness)) return 'Not installed';
    if (harness.daemon_status && harness.daemon_status !== 'ONLINE') return harness.daemon_status;
    return null;
}

export function firstHarnessModelName(harness: AgentHarnessInfo | AvailableHarnessOption): string | undefined {
    const firstModel = harness.models?.[0];
    return typeof firstModel === 'string' ? firstModel : firstModel?.name;
}

export function availableHarnessKey(harness: Pick<AgentHarnessInfo, 'daemon_id' | 'harness_kind'>): string {
    return `${harness.daemon_id ?? 'daemonless'}::${harness.harness_kind}`;
}

export function runtimeProfileDaemonKey(profile: AgentRuntimeProfileResponse): string | null {
    const daemonId = typeof profile.daemon_id === 'string' ? profile.daemon_id : null;
    return daemonId ? `${daemonId}::${profile.derived_harness_kind}` : null;
}

export function runtimeAvailabilityLabel(profile: AgentRuntimeProfileResponse): string | null {
    if (!profile.daemon_id) return null;
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
            return profile.daemon_status && profile.daemon_status !== 'ONLINE'
                ? profile.daemon_status
                : null;
    }
}

// Flatten the runtime-profile catalog into the flat, plain-language model list
// the ModelPicker consumes. Every pickable model across every saved profile
// (Lemma built-in, BYO providers, coding agents) becomes one row, tagged with
// its profile so the picker can group by provider. Provider/daemon *creation*
// is intentionally not represented here — that lives in the manage surface.
export function runtimeCatalogToModelOptions(
    catalog?: AgentRuntimeProfileListResponse,
    availableHarnesses?: AgentHarnessListResponse,
): AvailableModelInfo[] {
    if (!catalog?.items?.length) return [];
    const options: AvailableModelInfo[] = [];
    for (const profile of catalog.items) {
        for (const model of runtimeModels(profile, availableHarnesses)) {
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
