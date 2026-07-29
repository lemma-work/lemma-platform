import type {
    AgentRuntimeConfig,
    AgentRuntimeProfileListResponse,
    AvailableModelInfo,
    HarnessRuntimeProfileResponse,
    RuntimeModelCatalogEntry,
} from 'lemma-sdk';

export const DEFAULT_VALUE = '__default_runtime__';

export type RuntimeProfile = AgentRuntimeProfileListResponse['items'][number];
export type RuntimeModelOption = RuntimeModelCatalogEntry & { name: string };
export type AgentRuntimeSelectionMode = 'runtime' | 'model';
export type CustomProviderKind = 'openai' | 'anthropic';

export const HARNESS_LOGOS: Partial<Record<string, string>> = {
    antigravity: '/harnesslogos/antigravity.png',
    claude_code: '/harnesslogos/claudecode.png',
    codex: '/harnesslogos/codex.png',
    cursor: '/harnesslogos/cursor.png',
    opencode: '/harnesslogos/opencode.png',
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

export function isHarnessProfile(
    profile?: RuntimeProfile | null,
): profile is HarnessRuntimeProfileResponse {
    return profile?.runtime_type === 'HARNESS';
}

export function runtimeKey(runtime: AgentRuntimeConfig): string {
    return `${runtime.profile_id}::${runtime.model_name ?? ''}`;
}

export function firstRuntime(catalog?: AgentRuntimeProfileListResponse): AgentRuntimeConfig | null {
    return catalog?.default_runtime ?? null;
}

export function runtimeModels(
    profile?: RuntimeProfile,
    _unusedLegacyCatalog?: unknown,
): RuntimeModelOption[] {
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

export function findProfileByRuntime(
    catalog: AgentRuntimeProfileListResponse | undefined,
    runtime?: AgentRuntimeConfig | null,
) {
    if (!runtime) return undefined;
    return catalog?.items.find((profile) => profile.id === runtime.profile_id);
}

export function defaultAgentRuntimeFromProfile(
    profile?: RuntimeProfile | null,
    _unusedLegacyCatalog?: unknown,
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
    _unusedLegacyCatalog?: unknown,
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
    const prefix = profile?.name
        ?? (runtime.profile_id === catalog?.default_runtime?.profile_id
            ? 'Default Agent Runtime'
            : runtime.profile_id);
    return includeModel && modelName ? `${prefix} · ${shortModelName(modelName)}` : prefix;
}

export function modelAdvisory(model: Pick<RuntimeModelCatalogEntry, 'metadata'>): string | null {
    const metadata = (model.metadata ?? {}) as Record<string, unknown>;
    if (metadata.requires_credits) return 'Requires usage credits';
    const note = typeof metadata.note === 'string' ? metadata.note.trim() : '';
    if (note) return note;
    const contextWindow = typeof metadata.context_window === 'string'
        ? metadata.context_window.trim()
        : '';
    return contextWindow && contextWindow.toLowerCase() !== 'standard'
        ? `${contextWindow} context`
        : null;
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
    return modelName
        .replace(new RegExp(`/?${escapeRegExp(shortName)}$`), '')
        .replace(/\/$/, '') || modelName;
}

export function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function harnessLogo(harnessKey?: string | null): string | undefined {
    return harnessKey ? HARNESS_LOGOS[harnessKey.toLowerCase()] : undefined;
}

export function runtimeAvailabilityLabel(profile: RuntimeProfile): string | null {
    if (profile.availability_status === 'READY') return null;
    if (profile.availability_status === 'OFFLINE') return 'Offline';
    if (profile.availability_status === 'UNAVAILABLE_FOR_YOU') return 'Unavailable';
    if (profile.availability_status === 'CONFIG_REVISION_MISMATCH') {
        return 'Configuration changed';
    }
    if (profile.availability_status === 'STALE') return 'Needs refresh';
    return profile.availability_status?.replaceAll('_', ' ') ?? null;
}

export function runtimeCatalogToModelOptions(
    catalog?: AgentRuntimeProfileListResponse,
    _unusedLegacyCatalog?: unknown,
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
                harness_kind: profile.runtime_type === 'HARNESS' ? 'HARNESS' : 'LEMMA',
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
