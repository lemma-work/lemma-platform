'use client';

import Image from 'next/image';
import { useState } from 'react';
import { RuntimeProfileScope } from 'lemma-sdk';
import type {
    AgentHarnessInfo,
    AgentHarnessListResponse,
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
} from 'lemma-sdk';
import { Check, KeyRound, Plus, RefreshCw, Sparkles, TerminalSquare } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useCreateAgentRuntime } from '@/lib/hooks/use-agent-runtime';
import { cn } from '@/lib/utils';
import {
    CUSTOM_PROVIDER_OPTIONS,
    LOCAL_RUNTIME_SETUP_COMMANDS,
    availableHarnessKey,
    availableHarnessStatusLabel,
    firstHarnessModelName,
    HARNESS_LOGOS,
    isHarnessAvailable,
    runtimeAvailabilityLabel,
    splitModelNames,
    type CustomProviderKind,
} from './agent-runtime-helpers';

const CODING_AGENT_KINDS = new Set(['CLAUDE_CODE', 'CODEX', 'OPENCODE', 'ANTIGRAVITY', 'CURSOR']);

function isCodingAgentKind(kind?: string | null): boolean {
    return kind ? CODING_AGENT_KINDS.has(kind) : false;
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
    const [scope, setScope] = useState<RuntimeProfileScope>(RuntimeProfileScope.ORGANIZATION);

    const providers = (catalog?.items ?? []).filter((p) => !isCodingAgentKind(p.derived_harness_kind));
    const detectedLocalAgents = (availableHarnesses?.items ?? []).filter((h) => isCodingAgentKind(h.harness_kind));

    return (
        <div className="flex flex-col gap-8">
            <div className="flex items-center justify-between">
                <div className="inline-flex gap-1 rounded-md bg-[var(--surface-1)] p-1">
                    {[
                        { value: RuntimeProfileScope.ORGANIZATION, label: 'Workspace' },
                        { value: RuntimeProfileScope.PERSONAL, label: 'Personal' },
                    ].map((option) => (
                        <button
                            key={option.value}
                            type="button"
                            onClick={() => setScope(option.value)}
                            className={cn(
                                'rounded px-3 py-1.5 text-sm font-medium transition-colors',
                                scope === option.value
                                    ? 'bg-[var(--surface-2)] text-[var(--text-primary)] shadow-xs'
                                    : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
                            )}
                        >
                            {option.label}
                        </button>
                    ))}
                </div>
                {onRefresh ? (
                    <Button type="button" variant="ghost" size="sm" onClick={() => void onRefresh()} disabled={isRefreshing} className="gap-1.5">
                        <RefreshCw className={cn('size-3.5', isRefreshing && 'animate-spin')} />
                        Recheck
                    </Button>
                ) : null}
            </div>

            <ProvidersSection
                organizationId={organizationId}
                scope={scope}
                providers={providers}
                onRefresh={onRefresh}
            />

            <LocalAgentsSection
                organizationId={organizationId}
                scope={scope}
                harnesses={detectedLocalAgents}
                onRefresh={onRefresh}
            />
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
    scope,
    providers,
    onRefresh,
}: {
    organizationId: string;
    scope: RuntimeProfileScope;
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
                            <StatusBadge label={status.label} tone={status.tone} />
                        </div>
                    );
                })}

                {connect ? (
                    <ConnectProviderForm
                        target={connect}
                        organizationId={organizationId}
                        scope={scope}
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
                                    className="rounded-md border border-[var(--border-subtle)] px-3 py-1.5 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--field-border-hover)] hover:text-[var(--text-primary)]"
                                >
                                    {preset.name}
                                </button>
                            ))}
                            {CUSTOM_PROVIDER_OPTIONS.map((option) => (
                                <button
                                    key={option.kind}
                                    type="button"
                                    onClick={() => setConnect({ kind: option.kind, name: '', baseUrl: option.defaultBaseUrl })}
                                    className="flex items-center gap-1.5 rounded-md border border-dashed border-[var(--border-strong)] px-3 py-1.5 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--field-border-hover)] hover:text-[var(--text-primary)]"
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
    scope,
    onClose,
    onSaved,
}: {
    target: ConnectTarget;
    organizationId: string;
    scope: RuntimeProfileScope;
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
            <div className="flex items-center justify-end gap-2">
                <Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
                <Button type="button" size="sm" onClick={() => void save()} loading={createRuntime.isPending} loadingLabel="Connecting">
                    Connect
                </Button>
            </div>
        </div>
    );
}

function LocalAgentsSection({
    organizationId,
    scope,
    harnesses,
    onRefresh,
}: {
    organizationId: string;
    scope: RuntimeProfileScope;
    harnesses: AgentHarnessInfo[];
    onRefresh?: () => void | Promise<void>;
}) {
    const createRuntime = useCreateAgentRuntime();
    const [savingKey, setSavingKey] = useState<string | null>(null);

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

    const save = async (harness: AgentHarnessInfo) => {
        if (!harness.daemon_id) return toast.error('Start the Lemma daemon to add this local agent');
        setSavingKey(availableHarnessKey(harness));
        try {
            await createRuntime.mutateAsync({
                organizationId,
                request: {
                    source: 'USER_DAEMON',
                    daemon_id: harness.daemon_id,
                    harness_kind: harness.harness_kind,
                    scope,
                    name: `${harness.display_name} daemon`,
                    default_model_name: firstHarnessModelName(harness) || undefined,
                },
            });
            toast.success(`${harness.display_name} added`);
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
                title="Local agents"
                hint="Terminal coding agents that run on your machine. Start the Lemma daemon to detect the ones you have installed."
            />
            <div className="flex flex-col gap-2">
                {rows.map((row) => {
                    const harness = row.harness;
                    const detected = Boolean(harness);
                    const available = harness ? isHarnessAvailable(harness) : false;
                    const status = harness ? (availableHarnessStatusLabel(harness) ?? 'Ready') : 'Not detected';
                    const logo = HARNESS_LOGOS[row.kind];
                    const key = harness ? availableHarnessKey(harness) : row.kind;
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
                                <StatusBadge label={status} tone={available ? 'ok' : 'muted'} />
                                {available && harness ? (
                                    <Button
                                        type="button"
                                        size="sm"
                                        onClick={() => void save(harness)}
                                        loading={savingKey === key}
                                        loadingLabel="Adding"
                                        className="gap-1.5"
                                    >
                                        <Check className="size-3.5" />
                                        Add
                                    </Button>
                                ) : null}
                            </div>
                            {!available ? (
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
