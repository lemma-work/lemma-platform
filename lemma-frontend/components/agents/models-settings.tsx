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
                harnesses={detectedLocalAgents}
                savedDaemonScopeByKey={savedDaemonScopeByKey}
                onRefresh={onRefresh}
            />
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
