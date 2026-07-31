'use client';

import Image from 'next/image';
import { useState } from 'react';
import { RuntimeProfileScope } from 'lemma-sdk';
import type {
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
} from 'lemma-sdk';
import { Copy, KeyRound, Plus, RefreshCw, Sparkles, TerminalSquare, Trash2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
    useAgentHostHarnesses,
    useAgentHosts,
    useCreateAgentHostPairing,
    useCreateAgentRuntime,
    useRevokeAgentHost,
    type AgentHost,
    type AgentHostHarness,
    type AgentHostPairing,
} from '@/lib/hooks/use-agent-runtime';
import { getLemmaApiBaseUrl } from '@/lib/sdk/lemma-client';
import { cn } from '@/lib/utils';
import {
    CUSTOM_PROVIDER_OPTIONS,
    agentHostHarnessHealth,
    agentHostHarnessModelCount,
    agentHostStatusLabel,
    pairingCommands,
    harnessLogo,
    isLocalAgentKind,
    runtimeAvailabilityLabel,
    splitModelNames,
    type CustomProviderKind,
} from './agent-runtime-helpers';

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

type ConnectTarget = { kind: CustomProviderKind; name: string; baseUrl: string };

export function ModelsSettings({
    organizationId,
    catalog,
    onRefresh,
    isRefreshing = false,
}: {
    organizationId: string;
    catalog?: AgentRuntimeProfileListResponse;
    onRefresh?: () => void | Promise<void>;
    isRefreshing?: boolean;
}) {
    const providers = (catalog?.items ?? []).filter((p) => !isLocalAgentKind(p.derived_harness_kind));

    return (
        <div className="flex flex-col gap-8">
            <div className="flex items-start justify-between gap-4">
                <p className="text-sm text-[var(--text-tertiary)]">
                    Connect the models and local agents this workspace can use. Providers you connect here are shared
                    with everyone in the workspace; a paired computer runs its coding agents for the workspace without
                    its credentials ever leaving that machine.
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

            <AgentHostsSection organizationId={organizationId} catalog={catalog} />
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
                        name: trimmedName,
                        base_url: baseUrl.trim(),
                        api_key: apiKey.trim() || null,
                        default_model_name: defaultModelName,
                        model_names: modelNames,
                    }
                    : {
                        source: 'ANTHROPIC_COMPATIBLE',
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
            <div className="flex flex-wrap items-center justify-end gap-2">
                <Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
                <Button type="button" size="sm" onClick={() => void save()} loading={createRuntime.isPending} loadingLabel="Connecting">
                    Connect
                </Button>
            </div>
        </div>
    );
}

// A paired computer runs the local coding agents. The machine holds a scoped,
// separately revocable secret and reaches Lemma over outbound HTTPS, so nothing
// here needs an inbound port or the user's own session on that computer.
function AgentHostsSection({
    organizationId,
    catalog,
}: {
    organizationId: string;
    catalog?: AgentRuntimeProfileListResponse;
}) {
    const hosts = useAgentHosts();
    const createPairing = useCreateAgentHostPairing();
    const [pairing, setPairing] = useState<(AgentHostPairing & { display_name: string }) | null>(null);
    const [displayName, setDisplayName] = useState('My computer');

    // Harnesses that are already saved as runtime profiles, so a row can say
    // "already added" instead of leaving the user guessing whether picking this
    // agent in a chat is possible yet.
    const savedProfileNameByHarnessId = new Map<string, string>();
    for (const profile of catalog?.items ?? []) {
        if (profile.harness_id) savedProfileNameByHarnessId.set(profile.harness_id, profile.name);
    }

    // A revoked host stays readable through the API for audit, but it can never
    // take work again, so it has no place in a "what can I use" list.
    const activeHosts = (hosts.data?.items ?? []).filter((host) => host.status !== 'REVOKED');

    const pair = async () => {
        const name = displayName.trim();
        if (!name) return toast.error('Name this computer');
        try {
            const created = await createPairing.mutateAsync({ organizationId, displayName: name });
            setPairing({ ...created, display_name: name });
        } catch (error) {
            toast.error(`Couldn't create a pairing code: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    return (
        <section>
            <SectionHeader
                icon={<TerminalSquare className="size-4" />}
                title="Paired computers"
                hint="Pair a computer once and its Agent Host runs Codex, Claude Code, and OpenCode for this workspace. Credentials never leave that machine."
            />
            <div className="flex flex-col gap-3">
                {hosts.isLoading ? (
                    <p className="text-sm text-[var(--text-tertiary)]">Loading paired computers…</p>
                ) : null}

                {activeHosts.map((host) => (
                    <AgentHostCard
                        key={host.id}
                        host={host}
                        savedProfileNameByHarnessId={savedProfileNameByHarnessId}
                    />
                ))}

                {pairing ? (
                    <PairingInstructions
                        pairing={pairing}
                        onDone={() => {
                            setPairing(null);
                            void hosts.refetch();
                        }}
                    />
                ) : (
                    <div className="flex flex-col gap-3 rounded-md border border-dashed border-[var(--border-strong)] p-4">
                        <div>
                            <div className="text-sm font-medium text-[var(--text-primary)]">
                                {activeHosts.length ? 'Pair another computer' : 'Pair a computer'}
                            </div>
                            <p className="mt-1 text-sm text-[var(--text-tertiary)]">
                                You&apos;ll get a one-time code to run on that machine.
                            </p>
                        </div>
                        <div className="flex flex-wrap items-end gap-3">
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
                                onClick={() => void pair()}
                                loading={createPairing.isPending}
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
    pairing: AgentHostPairing & { display_name: string };
    onDone: () => void;
}) {
    const commands = pairingCommands(pairing, getLemmaApiBaseUrl());
    const expiresAt = new Date(pairing.expires_at);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(commands.join('\n'));
            toast.success('Commands copied');
        } catch {
            toast.error('Copy the commands manually — the browser blocked clipboard access');
        }
    };

    return (
        <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] p-4">
            <div className="flex items-start justify-between gap-3">
                <div className="text-sm font-medium text-[var(--text-primary)]">Run these on that computer</div>
                <Button type="button" variant="ghost" size="sm" onClick={() => void copy()} className="gap-1.5">
                    <Copy className="size-3.5" />
                    Copy
                </Button>
            </div>
            <div className="mt-2 flex flex-col gap-1.5">
                {commands.map((command) => (
                    <code
                        key={command}
                        className="overflow-x-auto rounded bg-[var(--surface-2)] px-3 py-2 font-mono text-xs whitespace-pre text-[var(--text-secondary)]"
                    >
                        {command}
                    </code>
                ))}
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-[var(--text-tertiary)]">
                    Skip the first line if that computer already has the Lemma CLI. This code works
                    once, and expires{' '}
                    {Number.isNaN(expiresAt.valueOf()) ? 'shortly' : expiresAt.toLocaleTimeString()}.
                </p>
                <Button type="button" size="sm" onClick={onDone}>
                    I ran them
                </Button>
            </div>
        </div>
    );
}

function AgentHostCard({
    host,
    savedProfileNameByHarnessId,
}: {
    host: AgentHost;
    savedProfileNameByHarnessId: Map<string, string>;
}) {
    const harnesses = useAgentHostHarnesses(host.id);
    const revoke = useRevokeAgentHost();
    const activeRuns = host.capacity?.active_runs ?? 0;
    const maxRuns = host.capacity?.max_runs ?? null;
    const online = host.status === 'ONLINE';

    const disconnect = async () => {
        if (!window.confirm(`Disconnect ${host.display_name}? Its credential is revoked immediately and new runs stop.`)) {
            return;
        }
        try {
            await revoke.mutateAsync(host.id);
            toast.success(`${host.display_name} disconnected`);
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
                        Agent Host {host.host_release} · {activeRuns}
                        {maxRuns === null ? '' : `/${maxRuns}`} running
                        {host.last_seen_at ? ` · seen ${new Date(host.last_seen_at).toLocaleTimeString()}` : ''}
                    </div>
                </div>
                <StatusBadge label={agentHostStatusLabel(host.status)} tone={online ? 'ok' : 'muted'} />
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void harnesses.refetch()}
                    disabled={harnesses.isFetching}
                    aria-label={`Recheck ${host.display_name}`}
                >
                    <RefreshCw className={cn('size-4', harnesses.isFetching && 'animate-spin')} />
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
                {harnesses.isLoading ? (
                    <p className="px-1 text-xs text-[var(--text-tertiary)]">Looking for agents on this computer…</p>
                ) : null}
                {(harnesses.data?.items ?? []).map((harness) => (
                    <AgentHostHarnessRow
                        key={harness.id}
                        harness={harness}
                        hostOnline={online}
                        savedProfileName={savedProfileNameByHarnessId.get(harness.id) ?? null}
                    />
                ))}
                {!harnesses.isLoading && !(harnesses.data?.items.length ?? 0) ? (
                    <p className="px-1 text-xs text-[var(--text-tertiary)]">
                        No agents published yet. That computer republishes what it finds every 15
                        minutes; <code className="font-mono">lemma agent-host refresh</code> asks it
                        to look again now.
                    </p>
                ) : null}
            </div>
        </div>
    );
}

function AgentHostHarnessRow({
    harness,
    hostOnline,
    savedProfileName,
}: {
    harness: AgentHostHarness;
    hostOnline: boolean;
    savedProfileName: string | null;
}) {
    const health = agentHostHarnessHealth(harness.health);
    const modelCount = agentHostHarnessModelCount(harness.config_options ?? []);
    const logo = harnessLogo(harness.harness_key);
    // A healthy harness on an offline computer still can't take work, so say so
    // instead of showing a green badge next to an unreachable machine.
    const usable = health.ready && hostOnline;
    const blockedReason = health.ready
        ? hostOnline
            ? null
            : 'That computer is offline. Runs resume when Agent Host reconnects.'
        : health.detail;

    return (
        <div className="rounded-md bg-[var(--surface-1)] px-3 py-3">
            <div className="flex flex-wrap items-center gap-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)]">
                    {logo ? (
                        <Image src={logo} alt="" width={16} height={16} className="size-4 object-contain" />
                    ) : (
                        <TerminalSquare className="size-3.5 text-[var(--text-tertiary)]" />
                    )}
                </span>
                <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-[var(--text-primary)]">{harness.display_name}</div>
                    <div className="text-xs text-[var(--text-tertiary)]">
                        adapter {harness.adapter_version}
                        {harness.upstream_version ? ` · agent ${harness.upstream_version}` : ''}
                        {modelCount ? ` · ${modelCount} model${modelCount === 1 ? '' : 's'}` : ''}
                    </div>
                </div>
                {savedProfileName ? <StatusBadge label={`Added as ${savedProfileName}`} tone="muted" /> : null}
                <StatusBadge label={health.label} tone={usable ? 'ok' : 'muted'} />
            </div>
            {blockedReason ? <p className="mt-2 text-xs text-[var(--text-tertiary)]">{blockedReason}</p> : null}
            {!savedProfileName && health.ready ? (
                <p className="mt-2 text-xs text-[var(--text-tertiary)]">
                    Make this pickable in chats with{' '}
                    <code className="font-mono">
                        lemma runtime profiles create AGENT_HOST --harness-id {harness.id}
                    </code>
                    .
                </p>
            ) : null}
            {harness.stale_reason ? (
                <p className="mt-1 text-xs text-[var(--text-tertiary)]">{harness.stale_reason}</p>
            ) : null}
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
