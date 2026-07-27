'use client';

import Image from 'next/image';
import { useState } from 'react';
import { RuntimeProfileScope } from 'lemma-sdk';
import type {
    AgentHostConfigOption,
    AgentHostIntegrationResponse,
    AgentHostResponse,
    AgentHarnessInfo,
    AgentHarnessListResponse,
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
} from 'lemma-sdk';
import { Check, Copy, KeyRound, Plus, RefreshCw, Sparkles, TerminalSquare, Trash2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
    useAgentHostIntegrations,
    useAgentHosts,
    useCreateAgentHostPairing,
    useCreateAgentRuntime,
    useRevokeAgentHost,
} from '@/lib/hooks/use-agent-runtime';
import { getLemmaApiBaseUrl } from '@/lib/sdk/lemma-client';
import { useProfile } from '@/lib/hooks/use-user';
import { cn } from '@/lib/utils';
import {
    CUSTOM_PROVIDER_OPTIONS,
    LOCAL_RUNTIME_SETUP_COMMANDS,
    availableHarnessKey,
    availableHarnessStatusLabel,
    firstHarnessModelName,
    HARNESS_LOGOS,
    isCodingAgentKind,
    isHarnessAvailable,
    runtimeAvailabilityLabel,
    runtimeProfileDaemonKey,
    splitModelNames,
    type CustomProviderKind,
} from './agent-runtime-helpers';

// The two scopes a connection can be saved under. SYSTEM profiles (Lemma's
// built-ins) aren't user-creatable, so the chooser only offers these two.
const SAVE_SCOPES: Array<{ value: RuntimeProfileScope; label: string; hint: string }> = [
    { value: RuntimeProfileScope.ORGANIZATION, label: 'Workspace', hint: 'Shared with everyone here' },
    { value: RuntimeProfileScope.PERSONAL, label: 'Personal', hint: 'Only you' },
];

function scopeBadge(scope: RuntimeProfileScope): { label: string; tone: 'ok' | 'muted' } | null {
    if (scope === RuntimeProfileScope.SYSTEM) return null;
    if (scope === RuntimeProfileScope.PERSONAL) return { label: 'Personal', tone: 'muted' };
    return { label: 'Workspace', tone: 'muted' };
}

// Quick-start presets for popular providers and routers. Clicking one prefills
// the connect form with the right kind, name, and base URL — the user only adds
// their key. "Custom" (in CUSTOM_PROVIDER_OPTIONS) stays for anything else.
type ProviderPreset = { id: string; kind: CustomProviderKind; name: string; baseUrl: string };
const PROVIDER_PRESETS: ProviderPreset[] = [
    { id: 'openrouter', kind: 'openai', name: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1' },
    { id: 'groq', kind: 'openai', name: 'Groq', baseUrl: 'https://api.groq.com/openai/v1' },
    { id: 'together', kind: 'openai', name: 'Together AI', baseUrl: 'https://api.together.xyz/v1' },
    { id: 'fireworks', kind: 'openai', name: 'Fireworks', baseUrl: 'https://api.fireworks.ai/inference/v1' },
    { id: 'deepseek', kind: 'openai', name: 'DeepSeek', baseUrl: 'https://api.deepseek.com' },
    { id: 'xai', kind: 'openai', name: 'xAI Grok', baseUrl: 'https://api.x.ai/v1' },
    { id: 'mistral', kind: 'openai', name: 'Mistral', baseUrl: 'https://api.mistral.ai/v1' },
    { id: 'openai', kind: 'openai', name: 'OpenAI', baseUrl: 'https://api.openai.com/v1' },
    { id: 'anthropic', kind: 'anthropic', name: 'Anthropic', baseUrl: 'https://api.anthropic.com' },
];

// The local agents we surface even when undetected, so the section reads as a
// menu of what's possible rather than an empty box. Detected ones get live
// status and an Add button; the rest show as "Not detected".
const KNOWN_LOCAL_AGENTS: Array<{ kind: string; name: string }> = [
    { kind: 'CLAUDE_CODE', name: 'Claude Code' },
    { kind: 'CODEX', name: 'Codex' },
    { kind: 'OPENCODE', name: 'OpenCode' },
    { kind: 'ANTIGRAVITY', name: 'Antigravity' },
    { kind: 'CURSOR', name: 'Cursor' },
];

type ConnectTarget = { kind: CustomProviderKind; name: string; baseUrl: string };

export function ModelsSettings({
    organizationId,
    catalog,
    availableHarnesses,
    onRefresh,
    isRefreshing = false,
}: {
    organizationId: string;
    catalog?: AgentRuntimeProfileListResponse;
    availableHarnesses?: AgentHarnessListResponse;
    onRefresh?: () => void | Promise<void>;
    isRefreshing?: boolean;
}) {
    const providers = (catalog?.items ?? []).filter((p) => !isCodingAgentKind(p.derived_harness_kind));
    const detectedLocalAgents = (availableHarnesses?.items ?? []).filter((h) => isCodingAgentKind(h.harness_kind));

    // Daemons already saved as runtime profiles, keyed by daemonId::harnessKind so
    // a detected harness can tell whether it's been added — and under which scope.
    const savedDaemonScopeByKey = new Map<string, RuntimeProfileScope>();
    for (const profile of catalog?.items ?? []) {
        if (!isCodingAgentKind(profile.derived_harness_kind)) continue;
        const key = runtimeProfileDaemonKey(profile);
        if (key) savedDaemonScopeByKey.set(key, profile.scope);
    }

    return (
        <div className="flex flex-col gap-8">
            <div className="flex items-start justify-between gap-4">
                <p className="text-sm text-[var(--text-tertiary)]">
                    Connect the models and local agents this workspace can use. Each connection is saved as{' '}
                    <span className="font-medium text-[var(--text-secondary)]">Workspace</span> (shared with everyone) or{' '}
                    <span className="font-medium text-[var(--text-secondary)]">Personal</span> (only you) — you choose when you add it.
                </p>
                {onRefresh ? (
                    <Button type="button" variant="ghost" size="sm" onClick={() => void onRefresh()} disabled={isRefreshing} className="shrink-0 gap-1.5">
                        <RefreshCw className={cn('size-3.5', isRefreshing && 'animate-spin')} />
                        Recheck
                    </Button>
                ) : null}
            </div>

            <ProvidersSection
                organizationId={organizationId}
                providers={providers}
                onRefresh={onRefresh}
            />

            <LocalAgentsSection
                organizationId={organizationId}
                profiles={catalog?.items ?? []}
                onRefresh={onRefresh}
            />

            {detectedLocalAgents.length > 0 || savedDaemonScopeByKey.size > 0 ? (
                <LegacyLocalAgentsSection
                    organizationId={organizationId}
                    harnesses={detectedLocalAgents}
                    savedDaemonScopeByKey={savedDaemonScopeByKey}
                    onRefresh={onRefresh}
                />
            ) : null}
        </div>
    );
}

// A small two-option chooser for where a new connection is saved. Inline at the
// point of saving — there's no global mode, so the list always reflects reality.
function ScopeChooser({ value, onChange }: { value: RuntimeProfileScope; onChange: (scope: RuntimeProfileScope) => void }) {
    return (
        <div className="flex flex-col gap-1.5">
            <Label className="text-[var(--text-secondary)]">Save to</Label>
            <div className="inline-flex w-fit gap-1 rounded-md bg-[var(--surface-2)] p-1">
                {SAVE_SCOPES.map((option) => (
                    <button
                        key={option.value}
                        type="button"
                        onClick={() => onChange(option.value)}
                        title={option.hint}
                        className={cn(
                            'rounded px-3 py-1.5 text-sm font-medium transition-colors',
                            value === option.value
                                ? 'bg-[var(--surface-1)] text-[var(--text-primary)] shadow-xs'
                                : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
                        )}
                    >
                        {option.label}
                    </button>
                ))}
            </div>
        </div>
    );
}

function SectionHeader({ icon, title, hint }: { icon: React.ReactNode; title: string; hint?: string }) {
    return (
        <div className="mb-3">
            <div className="flex items-center gap-2">
                <span className="text-[var(--text-tertiary)]">{icon}</span>
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h2>
            </div>
            {hint ? <p className="mt-1 text-sm text-[var(--text-tertiary)]">{hint}</p> : null}
        </div>
    );
}

function providerStatusLabel(profile: AgentRuntimeProfileResponse): { label: string; tone: 'ok' | 'muted' } {
    if (profile.scope === RuntimeProfileScope.SYSTEM) return { label: 'Built in', tone: 'ok' };
    const availability = runtimeAvailabilityLabel(profile);
    if (availability) return { label: availability, tone: 'muted' };
    return { label: 'Active', tone: 'ok' };
}

function ProvidersSection({
    organizationId,
    providers,
    onRefresh,
}: {
    organizationId: string;
    providers: AgentRuntimeProfileResponse[];
    onRefresh?: () => void | Promise<void>;
}) {
    const [connect, setConnect] = useState<ConnectTarget | null>(null);

    return (
        <section>
            <SectionHeader
                icon={<KeyRound className="size-4" />}
                title="Providers"
                hint="Lemma's built-in models, or connect your own OpenAI- or Anthropic-compatible key."
            />
            <div className="flex flex-col gap-2">
                {providers.map((profile) => {
                    const status = providerStatusLabel(profile);
                    const modelCount = profile.model_catalog?.length ?? 0;
                    const isSystem = profile.scope === RuntimeProfileScope.SYSTEM;
                    const scope = scopeBadge(profile.scope);
                    return (
                        <div key={profile.id} className="flex items-center gap-3 rounded-md border border-[var(--border-subtle)] px-4 py-3">
                            <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[var(--surface-1)] text-[var(--text-secondary)]">
                                {isSystem ? <Sparkles className="size-4 text-[var(--delight)]" /> : <KeyRound className="size-4" />}
                            </span>
                            <div className="min-w-0 flex-1">
                                <div className="truncate text-sm font-medium text-[var(--text-primary)]">{profile.name}</div>
                                <div className="text-xs text-[var(--text-tertiary)]">
                                    {isSystem ? 'Built in' : 'Your key'}
                                    {modelCount ? ` · ${modelCount} model${modelCount === 1 ? '' : 's'}` : ''}
                                </div>
                            </div>
                            {scope ? <StatusBadge label={scope.label} tone={scope.tone} /> : null}
                            <StatusBadge label={status.label} tone={status.tone} />
                        </div>
                    );
                })}

                {connect ? (
                    <ConnectProviderForm
                        target={connect}
                        organizationId={organizationId}
                        onClose={() => setConnect(null)}
                        onSaved={() => {
                            setConnect(null);
                            void onRefresh?.();
                        }}
                    />
                ) : (
                    <div className="mt-1">
                        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-tertiary)]">Connect a provider</p>
                        <div className="flex flex-wrap gap-2">
                            {PROVIDER_PRESETS.map((preset) => (
                                <button
                                    key={preset.id}
                                    type="button"
                                    onClick={() => setConnect({ kind: preset.kind, name: preset.name, baseUrl: preset.baseUrl })}
                                    className="models-settings-provider-button rounded-md border border-[var(--border-subtle)] px-3 py-1.5 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--field-border-hover)] hover:text-[var(--text-primary)]"
                                >
                                    {preset.name}
                                </button>
                            ))}
                            {CUSTOM_PROVIDER_OPTIONS.map((option) => (
                                <button
                                    key={option.kind}
                                    type="button"
                                    onClick={() => setConnect({ kind: option.kind, name: '', baseUrl: option.defaultBaseUrl })}
                                    className="models-settings-provider-button flex items-center gap-1.5 rounded-md border border-dashed border-[var(--border-strong)] px-3 py-1.5 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--field-border-hover)] hover:text-[var(--text-primary)]"
                                >
                                    <Plus className="size-3.5" />
                                    {option.kind === 'openai' ? 'Custom (OpenAI)' : 'Custom (Anthropic)'}
                                </button>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </section>
    );
}

function ConnectProviderForm({
    target,
    organizationId,
    onClose,
    onSaved,
}: {
    target: ConnectTarget;
    organizationId: string;
    onClose: () => void;
    onSaved: () => void;
}) {
    const kind = target.kind;
    const [name, setName] = useState(target.name);
    const [baseUrl, setBaseUrl] = useState(target.baseUrl);
    const [apiKey, setApiKey] = useState('');
    const [models, setModels] = useState('');
    const [defaultModel, setDefaultModel] = useState('');
    const [scope, setScope] = useState<RuntimeProfileScope>(RuntimeProfileScope.ORGANIZATION);
    const createRuntime = useCreateAgentRuntime();

    const save = async () => {
        const trimmedName = name.trim();
        const modelNames = splitModelNames(models);
        const defaultModelName = defaultModel.trim() || modelNames[0] || undefined;
        if (!trimmedName) return toast.error('Name this provider');
        if (kind === 'openai' && !baseUrl.trim()) return toast.error('Enter the provider base URL');
        if (kind === 'anthropic' && !apiKey.trim()) return toast.error('Enter the API key');
        try {
            await createRuntime.mutateAsync({
                organizationId,
                request: kind === 'openai'
                    ? {
                        source: 'OPENAI_COMPATIBLE',
                        scope,
                        name: trimmedName,
                        base_url: baseUrl.trim(),
                        api_key: apiKey.trim() || null,
                        default_model_name: defaultModelName,
                        model_names: modelNames,
                    }
                    : {
                        source: 'ANTHROPIC_COMPATIBLE',
                        scope,
                        name: trimmedName,
                        base_url: baseUrl.trim() || null,
                        api_key: apiKey.trim(),
                        default_model_name: defaultModelName,
                        model_names: modelNames,
                    },
            });
            toast.success(`${trimmedName} connected`);
            onSaved();
        } catch (error) {
            toast.error(`Couldn't connect: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    return (
        <div className="flex flex-col gap-4 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] p-4">
            <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Name">
                    <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={kind === 'openai' ? 'OpenRouter' : 'Anthropic'} />
                </Field>
                <Field label="Base URL">
                    <Input
                        value={baseUrl}
                        onChange={(e) => setBaseUrl(e.target.value)}
                        placeholder={kind === 'openai' ? 'https://openrouter.ai/api/v1' : 'https://api.anthropic.com'}
                    />
                </Field>
            </div>
            <Field label="API key">
                <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-..." />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Models" hint="One per line">
                    <textarea
                        value={models}
                        onChange={(e) => setModels(e.target.value)}
                        placeholder="one model per line"
                        className="form-field-control min-h-20 w-full resize-y px-3 py-2 text-sm leading-5 text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                    />
                </Field>
                <Field label="Default model" hint="Optional">
                    <Input value={defaultModel} onChange={(e) => setDefaultModel(e.target.value)} placeholder="First listed model is used by default" />
                </Field>
            </div>
            <div className="flex flex-wrap items-end justify-between gap-3">
                <ScopeChooser value={scope} onChange={setScope} />
                <div className="flex items-center gap-2">
                    <Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
                    <Button type="button" size="sm" onClick={() => void save()} loading={createRuntime.isPending} loadingLabel="Connecting">
                        Connect
                    </Button>
                </div>
            </div>
        </div>
    );
}

function LocalAgentsSection({
    organizationId,
    profiles,
    onRefresh,
}: {
    organizationId: string;
    profiles: AgentRuntimeProfileResponse[];
    onRefresh?: () => void | Promise<void>;
}) {
    const hosts = useAgentHosts();
    const pairing = useCreateAgentHostPairing();
    const [pairingResult, setPairingResult] = useState<{
        pairing_code: string;
        expires_at: string;
        display_name: string;
    } | null>(null);
    const [displayName, setDisplayName] = useState('My computer');

    const createPairing = async () => {
        const name = displayName.trim();
        if (!name) return toast.error('Name this computer');
        try {
            const result = await pairing.mutateAsync({
                organizationId,
                displayName: name,
            });
            setPairingResult({ ...result, display_name: name });
        } catch (error) {
            toast.error(`Couldn't create pairing code: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    const activeHosts = (hosts.data?.items ?? []).filter((host) => host.status !== 'REVOKED');

    return (
        <section>
            <SectionHeader
                icon={<TerminalSquare className="size-4" />}
                title="Local agents"
                hint="Your computer runs the provider agent. Lemma sends durable jobs over outbound HTTPS; credentials stay on this machine."
            />
            <div className="flex flex-col gap-3">
                {activeHosts.map((host) => (
                    <AgentHostCard
                        key={host.id}
                        host={host}
                        profiles={profiles}
                        organizationId={organizationId}
                        onRefresh={onRefresh}
                    />
                ))}

                {pairingResult ? (
                    <PairingInstructions
                        pairing={pairingResult}
                        onDone={() => {
                            setPairingResult(null);
                            void hosts.refetch();
                        }}
                    />
                ) : (
                    <div className="flex flex-col gap-3 rounded-md border border-dashed border-[var(--border-strong)] p-4">
                        <div>
                            <div className="text-sm font-medium text-[var(--text-primary)]">
                                {activeHosts.length ? 'Connect another computer' : 'Connect this computer'}
                            </div>
                            <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                                Pair once, then Agent Host discovers Codex, Claude Code, OpenCode, and Cursor through ACP.
                            </p>
                        </div>
                        <div className="flex flex-wrap items-end gap-2">
                            <Field label="Computer name">
                                <Input
                                    value={displayName}
                                    onChange={(event) => setDisplayName(event.target.value)}
                                    className="w-64"
                                />
                            </Field>
                            <Button
                                type="button"
                                size="sm"
                                onClick={() => void createPairing()}
                                loading={pairing.isPending}
                                loadingLabel="Creating code"
                            >
                                Create pairing code
                            </Button>
                        </div>
                    </div>
                )}
            </div>
        </section>
    );
}

function PairingInstructions({
    pairing,
    onDone,
}: {
    pairing: { pairing_code: string; expires_at: string; display_name: string };
    onDone: () => void;
}) {
    const command = `lemma agent-host connect --url ${getLemmaApiBaseUrl()} --pairing-code ${pairing.pairing_code} --name "${pairing.display_name.replaceAll('"', '\\"')}"`;
    const expiresAt = new Date(pairing.expires_at);
    const copy = async () => {
        await navigator.clipboard.writeText(command);
        toast.success('Pairing command copied');
    };
    return (
        <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] p-4">
            <div className="text-sm font-medium text-[var(--text-primary)]">Run this command on the computer</div>
            <div className="mt-2 flex items-start gap-2">
                <code className="min-w-0 flex-1 overflow-x-auto rounded bg-[var(--surface-2)] px-3 py-2 font-mono text-xs text-[var(--text-secondary)]">
                    {command}
                </code>
                <Button type="button" variant="ghost" size="sm" onClick={() => void copy()} aria-label="Copy pairing command">
                    <Copy className="size-4" />
                </Button>
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-[var(--text-tertiary)]">
                    One-time code expires {Number.isNaN(expiresAt.valueOf()) ? 'soon' : expiresAt.toLocaleTimeString()}.
                </p>
                <Button type="button" size="sm" onClick={onDone}>I ran the command</Button>
            </div>
        </div>
    );
}

function AgentHostCard({
    host,
    profiles,
    organizationId,
    onRefresh,
}: {
    host: AgentHostResponse;
    profiles: AgentRuntimeProfileResponse[];
    organizationId: string;
    onRefresh?: () => void | Promise<void>;
}) {
    const integrations = useAgentHostIntegrations(host.id);
    const revoke = useRevokeAgentHost();
    const capacity = host.capacity as Record<string, unknown>;
    const activeRuns = typeof capacity.active_runs === 'number' ? capacity.active_runs : 0;
    const maxRuns = typeof capacity.max_runs === 'number' ? capacity.max_runs : null;
    const statusTone = host.status === 'ONLINE' ? 'ok' : 'muted';

    const disconnect = async () => {
        if (!window.confirm(`Disconnect ${host.display_name}? New runs will stop immediately.`)) return;
        try {
            await revoke.mutateAsync(host.id);
            toast.success(`${host.display_name} disconnected`);
            void onRefresh?.();
        } catch (error) {
            toast.error(`Couldn't disconnect: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    return (
        <div className="rounded-md border border-[var(--border-subtle)]">
            <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[var(--surface-1)]">
                    <TerminalSquare className="size-4 text-[var(--text-secondary)]" />
                </span>
                <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-[var(--text-primary)]">{host.display_name}</div>
                    <div className="text-xs text-[var(--text-tertiary)]">
                        Agent Host {host.host_release} · {activeRuns}{maxRuns === null ? '' : `/${maxRuns}`} active
                        {host.last_seen_at ? ` · seen ${new Date(host.last_seen_at).toLocaleTimeString()}` : ''}
                    </div>
                </div>
                <StatusBadge label={host.status.replaceAll('_', ' ')} tone={statusTone} />
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void integrations.refetch()}
                    disabled={integrations.isFetching}
                    aria-label={`Refresh ${host.display_name}`}
                >
                    <RefreshCw className={cn('size-4', integrations.isFetching && 'animate-spin')} />
                </Button>
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void disconnect()}
                    loading={revoke.isPending}
                    aria-label={`Disconnect ${host.display_name}`}
                >
                    <Trash2 className="size-4" />
                </Button>
            </div>
            <div className="flex flex-col gap-2 border-t border-[var(--border-subtle)] p-3">
                {integrations.isLoading ? (
                    <p className="px-1 text-xs text-[var(--text-tertiary)]">Discovering local agents…</p>
                ) : null}
                {(integrations.data?.items ?? []).map((integration) => (
                    <AgentHostIntegrationRow
                        key={integration.id}
                        integration={integration}
                        host={host}
                        profiles={profiles}
                        organizationId={organizationId}
                        onRefresh={onRefresh}
                    />
                ))}
                {!integrations.isLoading && !(integrations.data?.items.length ?? 0) ? (
                    <p className="px-1 text-xs text-[var(--text-tertiary)]">
                        No integrations published yet. Run <code>lemma agent-host refresh</code> on this computer.
                    </p>
                ) : null}
            </div>
        </div>
    );
}

function AgentHostIntegrationRow({
    integration,
    host,
    profiles,
    organizationId,
    onRefresh,
}: {
    integration: AgentHostIntegrationResponse;
    host: AgentHostResponse;
    profiles: AgentRuntimeProfileResponse[];
    organizationId: string;
    onRefresh?: () => void | Promise<void>;
}) {
    const [configuring, setConfiguring] = useState(false);
    const savedProfiles = profiles.filter((profile) => profile.host_integration_id === integration.id);
    const ready = integration.health === 'READY' && host.status === 'ONLINE';
    const optionCount = (integration.config_options as AgentHostConfigOption[]).reduce(
        (count, option) => count + (option.category === 'model' ? option.options?.length ?? 0 : 0),
        0,
    );

    return (
        <div className="rounded-md bg-[var(--surface-1)] px-3 py-3">
            <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-[var(--text-primary)]">{integration.display_name}</div>
                    <div className="text-xs text-[var(--text-tertiary)]">
                        ACP · adapter {integration.adapter_version}
                        {integration.upstream_version ? ` · provider ${integration.upstream_version}` : ''}
                        {optionCount ? ` · ${optionCount} models` : ''}
                    </div>
                </div>
                {savedProfiles.length ? (
                    <StatusBadge label={`${savedProfiles.length} saved`} tone="ok" />
                ) : null}
                <StatusBadge label={integration.health.replaceAll('_', ' ')} tone={ready ? 'ok' : 'muted'} />
                {ready ? (
                    <Button type="button" size="sm" onClick={() => setConfiguring((value) => !value)}>
                        {configuring ? 'Close' : 'Add profile'}
                    </Button>
                ) : null}
            </div>
            {integration.stale_reason ? (
                <p className="mt-2 text-xs text-[var(--state-danger,var(--text-tertiary))]">{integration.stale_reason}</p>
            ) : null}
            {configuring ? (
                <AddAgentHostProfileForm
                    integration={integration}
                    profiles={profiles}
                    organizationId={organizationId}
                    onCancel={() => setConfiguring(false)}
                    onSaved={() => {
                        setConfiguring(false);
                        void onRefresh?.();
                    }}
                />
            ) : null}
        </div>
    );
}

function AddAgentHostProfileForm({
    integration,
    profiles,
    organizationId,
    onCancel,
    onSaved,
}: {
    integration: AgentHostIntegrationResponse;
    profiles: AgentRuntimeProfileResponse[];
    organizationId: string;
    onCancel: () => void;
    onSaved: () => void;
}) {
    const createRuntime = useCreateAgentRuntime();
    const options = integration.config_options as AgentHostConfigOption[];
    const [name, setName] = useState(integration.display_name);
    const [scope, setScope] = useState<RuntimeProfileScope>(RuntimeProfileScope.PERSONAL);
    const [selections, setSelections] = useState<Record<string, string>>(() =>
        Object.fromEntries(options.map((option) => [option.id, 'FOLLOW_ADAPTER_DEFAULT'])),
    );
    const [fallbackProfileId, setFallbackProfileId] = useState('none');
    const fallbackProfiles = profiles.filter(
        (profile) => profile.protocol !== 'AGENT_HOST_V2' && profile.status === 'ACTIVE',
    );

    const save = async () => {
        const trimmedName = name.trim();
        if (!trimmedName) return toast.error('Name this local agent profile');
        try {
            await createRuntime.mutateAsync({
                organizationId,
                request: {
                    source: 'AGENT_HOST',
                    host_integration_id: integration.id,
                    integration_snapshot_revision: integration.config_revision,
                    config_selections: selections,
                    fallback_profile_id: fallbackProfileId === 'none' ? null : fallbackProfileId,
                    scope,
                    name: trimmedName,
                },
            });
            toast.success(`${trimmedName} added`);
            onSaved();
        } catch (error) {
            toast.error(`Couldn't add profile: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    return (
        <div className="mt-3 flex flex-col gap-3 border-t border-[var(--border-subtle)] pt-3">
            <Field label="Profile name">
                <Input value={name} onChange={(event) => setName(event.target.value)} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
                {options.map((option) => {
                    const values = agentHostOptionValues(option);
                    if (!values.length) return null;
                    return (
                        <Field key={option.id} label={option.name} hint={option.description ?? undefined}>
                            <Select
                                value={selections[option.id] ?? 'FOLLOW_ADAPTER_DEFAULT'}
                                onValueChange={(value) => setSelections((current) => ({ ...current, [option.id]: value }))}
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="FOLLOW_ADAPTER_DEFAULT">Adapter default</SelectItem>
                                    {values.map((item) => (
                                        <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </Field>
                    );
                })}
                {fallbackProfiles.length ? (
                    <Field label="If this computer is offline" hint="Optional explicit fallback">
                        <Select value={fallbackProfileId} onValueChange={setFallbackProfileId}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="none">Fail after wait</SelectItem>
                                {fallbackProfiles.map((profile) => (
                                    <SelectItem key={profile.id} value={profile.id}>{profile.name}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </Field>
                ) : null}
            </div>
            <div className="flex flex-wrap items-end justify-between gap-3">
                <ScopeChooser value={scope} onChange={setScope} />
                <div className="flex items-center gap-2">
                    <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
                    <Button type="button" size="sm" onClick={() => void save()} loading={createRuntime.isPending} loadingLabel="Adding">
                        Add profile
                    </Button>
                </div>
            </div>
        </div>
    );
}

function agentHostOptionValues(option: AgentHostConfigOption): Array<{ value: string; label: string }> {
    return (option.options ?? []).flatMap((raw) => {
        const value = typeof raw.value === 'string'
            ? raw.value
            : typeof raw.id === 'string'
                ? raw.id
                : null;
        if (!value || value === 'FOLLOW_ADAPTER_DEFAULT') return [];
        const label = typeof raw.name === 'string'
            ? raw.name
            : typeof raw.label === 'string'
                ? raw.label
                : value;
        return [{ value, label }];
    });
}

function LegacyLocalAgentsSection({
    organizationId,
    harnesses,
    savedDaemonScopeByKey,
    onRefresh,
}: {
    organizationId: string;
    harnesses: AgentHarnessInfo[];
    savedDaemonScopeByKey: Map<string, RuntimeProfileScope>;
    onRefresh?: () => void | Promise<void>;
}) {
    const createRuntime = useCreateAgentRuntime();
    const { data: profile } = useProfile();
    const [savingKey, setSavingKey] = useState<string | null>(null);
    const [addingKey, setAddingKey] = useState<string | null>(null);

    // Who's adding this daemon — used to pre-name it so a workspace with several
    // people's machines doesn't end up with five identical "Claude Code" entries.
    const userLabel = (profile?.full_name?.trim() || profile?.first_name?.trim() || profile?.email?.split('@')[0] || '').trim();
    const defaultDaemonName = (displayName: string) =>
        userLabel ? `${userLabel}'s ${displayName}` : `${displayName} daemon`;

    // Show the full known roster, each matched to a detected harness if present,
    // then append anything detected that we don't have a name for yet.
    const rows: Array<{ kind: string; name: string; harness?: AgentHarnessInfo }> = [
        ...KNOWN_LOCAL_AGENTS.map((known) => ({
            ...known,
            harness: harnesses.find((h) => h.harness_kind === known.kind),
        })),
        ...harnesses
            .filter((h) => !KNOWN_LOCAL_AGENTS.some((k) => k.kind === h.harness_kind))
            .map((h) => ({ kind: h.harness_kind as string, name: h.display_name, harness: h })),
    ];

    const save = async (harness: AgentHarnessInfo, scope: RuntimeProfileScope, name: string) => {
        if (!harness.daemon_id) return toast.error('Start the Lemma daemon to add this local agent');
        const finalName = name.trim() || defaultDaemonName(harness.display_name);
        setSavingKey(availableHarnessKey(harness));
        try {
            await createRuntime.mutateAsync({
                organizationId,
                request: {
                    source: 'USER_DAEMON',
                    daemon_id: harness.daemon_id,
                    harness_kind: harness.harness_kind,
                    scope,
                    name: finalName,
                    default_model_name: firstHarnessModelName(harness) || undefined,
                },
            });
            const scopeLabel = scope === RuntimeProfileScope.PERSONAL ? 'Personal' : 'Workspace';
            toast.success(`${finalName} added to ${scopeLabel}`);
            setAddingKey(null);
            void onRefresh?.();
        } catch (error) {
            toast.error(`Couldn't add ${harness.display_name}: ${error instanceof Error ? error.message : 'Unknown error'}`);
        } finally {
            setSavingKey(null);
        }
    };

    return (
        <section>
            <SectionHeader
                icon={<TerminalSquare className="size-4" />}
                title="Legacy daemon connections"
                hint="Existing v1 connections remain visible during migration. New connections should use Agent Host above."
            />
            <div className="flex flex-col gap-2">
                {rows.map((row) => {
                    const harness = row.harness;
                    const detected = Boolean(harness);
                    const available = harness ? isHarnessAvailable(harness) : false;
                    const status = harness ? (availableHarnessStatusLabel(harness) ?? 'Ready') : 'Not detected';
                    const logo = HARNESS_LOGOS[row.kind];
                    const key = harness ? availableHarnessKey(harness) : row.kind;
                    // Has this exact daemon already been saved as a runtime profile?
                    // If so the row reads as "Saved" instead of offering Add again.
                    const savedScope = harness ? savedDaemonScopeByKey.get(availableHarnessKey(harness)) : undefined;
                    const isSaved = savedScope !== undefined;
                    return (
                        <div key={key} className={cn('rounded-md border border-[var(--border-subtle)] px-4 py-3', !detected && 'opacity-70')}>
                            <div className="flex items-center gap-3">
                                <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[var(--surface-1)]">
                                    {logo ? <Image src={logo} alt="" width={20} height={20} className="size-5 object-contain" /> : <TerminalSquare className="size-4 text-[var(--text-tertiary)]" />}
                                </span>
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-sm font-medium text-[var(--text-primary)]">{row.name}</div>
                                    <div className="text-xs text-[var(--text-tertiary)]">Local · this machine</div>
                                </div>
                                {isSaved && savedScope ? (
                                    <>
                                        {scopeBadge(savedScope) ? <StatusBadge label={scopeBadge(savedScope)!.label} tone="muted" /> : null}
                                        <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[var(--state-success-soft,var(--surface-1))] px-2 py-0.5 text-xs font-medium text-[var(--state-success,var(--text-secondary))]">
                                            <Check className="size-3" />
                                            Saved
                                        </span>
                                    </>
                                ) : (
                                    <>
                                        <StatusBadge label={status} tone={available ? 'ok' : 'muted'} />
                                        {available && harness && addingKey !== key ? (
                                            <Button type="button" size="sm" onClick={() => setAddingKey(key)} className="gap-1.5">
                                                <Plus className="size-3.5" />
                                                Add
                                            </Button>
                                        ) : null}
                                    </>
                                )}
                            </div>
                            {available && harness && addingKey === key && !isSaved ? (
                                <AddDaemonForm
                                    defaultName={defaultDaemonName(harness.display_name)}
                                    loading={savingKey === key}
                                    onCancel={() => setAddingKey(null)}
                                    onSave={(name, scope) => void save(harness, scope, name)}
                                />
                            ) : null}
                            {!available && !isSaved ? (
                                <div className="mt-3 flex flex-col gap-1.5 border-t border-[var(--border-subtle)] pt-3">
                                    <p className="text-xs text-[var(--text-tertiary)]">
                                        {detected ? 'Start the Lemma daemon on this machine:' : `Install ${row.name}, then start the Lemma daemon:`}
                                    </p>
                                    {LOCAL_RUNTIME_SETUP_COMMANDS.map((command) => (
                                        <code key={command} className="rounded bg-[var(--surface-1)] px-2.5 py-1.5 font-mono text-xs text-[var(--text-secondary)]">
                                            {command}
                                        </code>
                                    ))}
                                </div>
                            ) : null}
                        </div>
                    );
                })}
            </div>
        </section>
    );
}

// Naming a daemon at save time is the only chance to do it (there's no rename
// API yet), and a good name is what lets people tell "Ada's Claude Code" from
// "Sam's Claude Code" once several machines are connected to one workspace.
function AddDaemonForm({
    defaultName,
    loading,
    onCancel,
    onSave,
}: {
    defaultName: string;
    loading: boolean;
    onCancel: () => void;
    onSave: (name: string, scope: RuntimeProfileScope) => void;
}) {
    const [name, setName] = useState(defaultName);
    const [scope, setScope] = useState<RuntimeProfileScope>(RuntimeProfileScope.ORGANIZATION);
    return (
        <div className="mt-3 flex flex-col gap-3 border-t border-[var(--border-subtle)] pt-3">
            <Field label="Name" hint="How this daemon shows up in your workspace">
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={defaultName} />
            </Field>
            <div className="flex flex-wrap items-end justify-between gap-3">
                <ScopeChooser value={scope} onChange={setScope} />
                <div className="flex items-center gap-2">
                    <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
                    <Button type="button" size="sm" onClick={() => onSave(name, scope)} loading={loading} loadingLabel="Adding" className="gap-1.5">
                        <Check className="size-3.5" />
                        Add
                    </Button>
                </div>
            </div>
        </div>
    );
}

function StatusBadge({ label, tone }: { label: string; tone: 'ok' | 'muted' }) {
    return (
        <span
            className={cn(
                'shrink-0 rounded-full px-2 py-0.5 text-xs font-medium',
                tone === 'ok'
                    ? 'bg-[var(--state-success-soft,var(--surface-1))] text-[var(--state-success,var(--text-secondary))]'
                    : 'bg-[var(--surface-1)] text-[var(--text-tertiary)]',
            )}
        >
            {label}
        </span>
    );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
    return (
        <div className="flex flex-col gap-1.5">
            <Label className="text-[var(--text-secondary)]">
                {label}
                {hint ? <span className="ml-1 font-normal text-[var(--text-tertiary)]">{hint}</span> : null}
            </Label>
            {children}
        </div>
    );
}
