'use client';

import Image from 'next/image';
import { useCallback, useState } from 'react';
import { RuntimeProfileKind, RuntimeProfileScope } from 'lemma-sdk';
import type { AgentRuntimeProfileResponse } from 'lemma-sdk';
import { Copy, KeyRound, Pencil, Plus, RefreshCw, RotateCcw, Sparkles, TerminalSquare, Trash2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch, SwitchThumb, SwitchTrack } from '@/components/ui/switch';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { SettingsList, SettingsRow } from '@/components/settings/settings-kit';
import { declineAutoConnect } from '@/lib/desktop/auto-connect';
import {
    useAgentHostHarnesses,
    useAgentHosts,
    useArchiveAgentRuntime,
    useCreateAgentHostPairing,
    useManagedAgentRuntimes,
    useRestoreAgentRuntime,
    useRevokeAgentHost,
    type AgentHost,
    type AgentHostHarness,
    type AgentHostPairing,
} from '@/lib/hooks/use-agent-runtime';
import { getLemmaApiBaseUrl } from '@/lib/sdk/lemma-client';
import { ThisComputerCard } from './this-computer-card';
import { HarnessProfileDialog, type HarnessDialogTarget } from './harness-profile-dialog';
import { ProviderProfileDialog, type ProviderDialogTarget } from './provider-profile-dialog';
import { cn } from '@/lib/utils';
import {
    CUSTOM_PROVIDER_OPTIONS,
    agentHostHarnessHealth,
    agentHostHarnessModelCount,
    agentHostStatusLabel,
    isArchivedProfile,
    pairingCommands,
    harnessLogo,
    profileHarnessKey,
    runtimeAvailabilityLabel,
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

export function ModelsSettings({
    organizationId,
    onRefresh,
}: {
    organizationId: string;
    /** Extra work to do on "Recheck" — the page's own catalog query, if it has one. */
    onRefresh?: () => void | Promise<void>;
}) {
    const [showArchived, setShowArchived] = useState(false);
    // The management listing, not the catalog: it can include archived profiles,
    // which must never reach the composer's model picker.
    const managed = useManagedAgentRuntimes(organizationId, { includeArchived: showArchived });
    const profiles = managed.data?.items ?? [];
    const isRefreshing = managed.isFetching;

    const providers = profiles.filter((profile) => profile.kind === RuntimeProfileKind.MODEL_PROVIDER);
    // Exactly what the old `isLocalAgentKind` filter used to hide: a saved coding
    // agent was represented nowhere as a profile, only as a badge on a harness row.
    const codingAgents = profiles.filter((profile) => profile.kind === RuntimeProfileKind.HARNESS);

    const refreshAll = () => {
        void managed.refetch();
        void onRefresh?.();
    };

    return (
        <div className="flex flex-col gap-8">
            <div className="flex items-start justify-between gap-4">
                <p className="text-sm text-[var(--text-tertiary)]">
                    Connect the models and local agents this workspace can use. Providers you connect here are shared
                    with everyone in the workspace; a paired computer runs its coding agents for the workspace without
                    its credentials ever leaving that machine.
                </p>
                <div className="flex shrink-0 items-center gap-3">
                    <label className="flex cursor-pointer items-center gap-2 text-sm text-[var(--text-tertiary)]">
                        <Switch checked={showArchived} onCheckedChange={setShowArchived}>
                            <SwitchTrack className={showArchived ? 'bg-[var(--action-primary)]' : undefined}>
                                <SwitchThumb className={showArchived ? 'translate-x-4' : undefined} />
                            </SwitchTrack>
                        </Switch>
                        Show archived
                    </label>
                    <Button type="button" variant="quiet" size="sm" onClick={refreshAll} disabled={isRefreshing} className="gap-1.5">
                        <RefreshCw className={cn('size-3.5', isRefreshing && 'lemma-spin')} />
                        Recheck
                    </Button>
                </div>
            </div>

            <ProvidersSection
                organizationId={organizationId}
                providers={providers}
                onRefresh={refreshAll}
            />

            <CodingAgentsSection
                organizationId={organizationId}
                profiles={codingAgents}
                onRefresh={refreshAll}
            />

            <AgentHostsSection
                organizationId={organizationId}
                savedProfiles={codingAgents}
                onRefresh={refreshAll}
            />
        </div>
    );
}

/**
 * Edit / archive / restore for one saved profile. SYSTEM-scope profiles are
 * Lemma's own built-ins — there is nothing here a workspace may change, so they
 * get no menu at all rather than a menu of disabled items.
 */
function ProfileRowActions({
    profile,
    organizationId,
    onEdit,
    onRefresh,
}: {
    profile: AgentRuntimeProfileResponse;
    organizationId: string;
    onEdit: () => void;
    onRefresh?: () => void;
}) {
    const [confirmArchive, setConfirmArchive] = useState(false);
    const archive = useArchiveAgentRuntime();
    const restore = useRestoreAgentRuntime();
    const archived = isArchivedProfile(profile);

    if (profile.scope === RuntimeProfileScope.SYSTEM) return null;

    const runRestore = async () => {
        try {
            await restore.mutateAsync({ organizationId, profileId: profile.id });
            toast.success(`${profile.name} restored`);
            onRefresh?.();
        } catch (error) {
            toast.error(`Couldn't restore: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    const runArchive = async () => {
        try {
            await archive.mutateAsync({ organizationId, profileId: profile.id });
            setConfirmArchive(false);
            toast.success(`${profile.name} archived`);
            onRefresh?.();
        } catch (error) {
            toast.error(`Couldn't archive: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    return (
        <>
            <ResourceActionsMenu ariaLabel={`Actions for ${profile.name}`}>
                <DropdownMenuItem
                    onSelect={(event) => {
                        event.preventDefault();
                        onEdit();
                    }}
                >
                    <Pencil className="mr-2 h-4 w-4" />
                    Edit…
                </DropdownMenuItem>
                {archived ? (
                    <DropdownMenuItem
                        onSelect={(event) => {
                            event.preventDefault();
                            void runRestore();
                        }}
                    >
                        <RotateCcw className="mr-2 h-4 w-4" />
                        Restore
                    </DropdownMenuItem>
                ) : (
                    <DestructiveResourceActionItem onSelect={() => setConfirmArchive(true)}>
                        Remove
                    </DestructiveResourceActionItem>
                )}
            </ResourceActionsMenu>

            <DestructiveConfirmationDialog
                open={confirmArchive}
                onOpenChange={setConfirmArchive}
                title={`Remove ${profile.name}?`}
                description="It leaves the model picker straight away. Nothing is deleted — you can restore it from “Show archived”."
                resourceName={profile.name}
                confirmationText=""
                consequences={[
                    'Agents, conversations and pods pinned to it will fail to start until they are pointed somewhere else.',
                    'Past runs keep their history and stay readable.',
                ]}
                confirmLabel="Remove"
                pendingLabel="Removing..."
                isPending={archive.isPending}
                onConfirm={() => void runArchive()}
            />
        </>
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
    onRefresh?: () => void;
}) {
    const [dialog, setDialog] = useState<ProviderDialogTarget | null>(null);

    return (
        <section>
            <SectionHeader
                icon={<KeyRound className="size-4" />}
                title="Providers"
                hint="Lemma's built-in models, or connect your own OpenAI- or Anthropic-compatible key."
            />
            <div className="flex flex-col gap-2">
                <SettingsList>
                    {providers.map((profile) => {
                        const status = providerStatusLabel(profile);
                        const modelCount = profile.model_catalog?.length ?? 0;
                        const isSystem = profile.scope === RuntimeProfileScope.SYSTEM;
                        const scope = scopeBadge(profile.scope);
                        return (
                            <SettingsRow key={profile.id}>
                                <div className="flex min-w-0 items-center gap-3">
                                    <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[var(--surface-1)] text-[var(--text-secondary)]">
                                        {isSystem ? <Sparkles className="size-4 text-[var(--delight)]" /> : <KeyRound className="size-4" />}
                                    </span>
                                    <div className="min-w-0">
                                        <div className="truncate text-sm font-medium text-[var(--text-primary)]">{profile.name}</div>
                                        <div className="text-xs text-[var(--text-tertiary)]">
                                            {isSystem ? 'Built in' : 'Your key'}
                                            {modelCount ? ` · ${modelCount} model${modelCount === 1 ? '' : 's'}` : ''}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex shrink-0 items-center gap-2">
                                    {isArchivedProfile(profile) ? <StatusBadge label="Archived" tone="muted" /> : null}
                                    {scope ? <StatusBadge label={scope.label} tone={scope.tone} /> : null}
                                    <StatusBadge label={status.label} tone={status.tone} />
                                    <ProfileRowActions
                                        profile={profile}
                                        organizationId={organizationId}
                                        onEdit={() => setDialog({ mode: 'edit', profile })}
                                        onRefresh={onRefresh}
                                    />
                                </div>
                            </SettingsRow>
                        );
                    })}
                </SettingsList>

                <div className="mt-1">
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-tertiary)]">Connect a provider</p>
                    <div className="flex flex-wrap gap-2">
                        {PROVIDER_PRESETS.map((preset) => (
                            <button
                                key={preset.id}
                                type="button"
                                onClick={() => setDialog({ mode: 'connect', kind: preset.kind, name: preset.name, baseUrl: preset.baseUrl })}
                                className="models-settings-provider-button rounded-md border border-[var(--border-subtle)] px-3 py-1.5 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--field-border-hover)] hover:text-[var(--text-primary)]"
                            >
                                {preset.name}
                            </button>
                        ))}
                        {CUSTOM_PROVIDER_OPTIONS.map((option) => (
                            <button
                                key={option.kind}
                                type="button"
                                onClick={() => setDialog({ mode: 'connect', kind: option.kind, name: '', baseUrl: option.defaultBaseUrl })}
                                className="models-settings-provider-button flex items-center gap-1.5 rounded-md border border-dashed border-[var(--border-strong)] px-3 py-1.5 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--field-border-hover)] hover:text-[var(--text-primary)]"
                            >
                                <Plus className="size-3.5" />
                                {option.kind === 'openai' ? 'Custom (OpenAI)' : 'Custom (Anthropic)'}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            <ProviderProfileDialog
                target={dialog}
                organizationId={organizationId}
                onClose={() => setDialog(null)}
                onSaved={onRefresh}
            />
        </section>
    );
}

// Saved coding agents, which are runtime profiles like any other — they just run
// on a paired computer instead of behind an API key. Keeping them out of
// Providers is deliberate: a laptop and an API key do not belong in one list.
function CodingAgentsSection({
    organizationId,
    profiles,
    onRefresh,
}: {
    organizationId: string;
    profiles: AgentRuntimeProfileResponse[];
    onRefresh?: () => void;
}) {
    const [dialog, setDialog] = useState<HarnessDialogTarget | null>(null);

    return (
        <section>
            <SectionHeader
                icon={<TerminalSquare className="size-4" />}
                title="Coding agents"
                hint="Agents you've added to the model picker. Each one runs on the computer it was added from."
            />
            <div className="flex flex-col gap-2">
                {profiles.length === 0 ? (
                    <p className="text-sm text-[var(--text-tertiary)]">
                        None yet. Add one from a paired computer below.
                    </p>
                ) : null}
                <SettingsList>
                    {profiles.map((profile) => {
                        const logo = harnessLogo(profileHarnessKey(profile));
                        const modelCount = profile.model_catalog?.length ?? 0;
                        const scope = scopeBadge(profile.scope);
                        const availability = runtimeAvailabilityLabel(profile);
                        return (
                            <SettingsRow key={profile.id}>
                                <div className="flex min-w-0 items-center gap-3">
                                    <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[var(--surface-1)]">
                                        {logo ? (
                                            <Image src={logo} alt="" width={18} height={18} className="size-4.5 object-contain" />
                                        ) : (
                                            <TerminalSquare className="size-4 text-[var(--text-secondary)]" />
                                        )}
                                    </span>
                                    <div className="min-w-0">
                                        <div className="truncate text-sm font-medium text-[var(--text-primary)]">{profile.name}</div>
                                        <div className="text-xs text-[var(--text-tertiary)]">
                                            {profile.default_model_name ?? 'Agent picks the model'}
                                            {modelCount ? ` · ${modelCount} model${modelCount === 1 ? '' : 's'}` : ''}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex shrink-0 items-center gap-2">
                                    {isArchivedProfile(profile) ? <StatusBadge label="Archived" tone="muted" /> : null}
                                    {scope ? <StatusBadge label={scope.label} tone={scope.tone} /> : null}
                                    <StatusBadge
                                        label={availability ?? 'Active'}
                                        tone={availability ? 'muted' : 'ok'}
                                    />
                                    <ProfileRowActions
                                        profile={profile}
                                        organizationId={organizationId}
                                        onEdit={() => setDialog({ mode: 'edit', profile })}
                                        onRefresh={onRefresh}
                                    />
                                </div>
                            </SettingsRow>
                        );
                    })}
                </SettingsList>
            </div>

            <HarnessProfileDialog
                target={dialog}
                organizationId={organizationId}
                onClose={() => setDialog(null)}
                onSaved={onRefresh}
            />
        </section>
    );
}

// A paired computer runs the local coding agents. The machine holds a scoped,
// separately revocable secret and reaches Lemma over outbound HTTPS, so nothing
// here needs an inbound port or the user's own session on that computer.
function AgentHostsSection({
    organizationId,
    savedProfiles,
    onRefresh,
}: {
    organizationId: string;
    savedProfiles: AgentRuntimeProfileResponse[];
    onRefresh?: () => void;
}) {
    const hosts = useAgentHosts();
    const createPairing = useCreateAgentHostPairing();
    const [pairing, setPairing] = useState<(AgentHostPairing & { display_name: string }) | null>(null);
    const [displayName, setDisplayName] = useState('My computer');
    // Which paired computer is the one the user is sitting at. Only the desktop
    // app can answer that; in a browser it stays null and the list is unchanged.
    const [thisHostId, setThisHostId] = useState<string | null>(null);
    const onHostIdChange = useCallback((hostId: string | null) => setThisHostId(hostId), []);

    // Harnesses that are already saved as runtime profiles, so a row can say
    // "already added" instead of leaving the user guessing whether picking this
    // agent in a chat is possible yet. Built from the management listing rather
    // than the catalog: an archived profile is absent from the catalog, so the
    // row would offer "Add to chat models" again and then 409 on the unique-name
    // index.
    const savedProfileByHarnessId = new Map<string, AgentRuntimeProfileResponse>();
    for (const profile of savedProfiles) {
        if (profile.harness_id) savedProfileByHarnessId.set(profile.harness_id, profile);
    }

    // A revoked host stays readable through the API for audit, but it can never
    // take work again, so it has no place in a "what can I use" list.
    const activeHosts = (hosts.data?.items ?? []).filter((host) => host.status !== 'REVOKED');

    const pair = async () => {
        const name = displayName.trim();
        if (!name) return toast.error('Name this computer');
        try {
            const created = await createPairing.mutateAsync({ displayName: name });
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
                <ThisComputerCard
                    onHostIdChange={onHostIdChange}
                    onPaired={() => void hosts.refetch()}
                />

                {hosts.isLoading ? (
                    <p className="text-sm text-[var(--text-tertiary)]">Loading paired computers…</p>
                ) : null}

                {activeHosts.map((host) => (
                    <AgentHostCard
                        key={host.id}
                        host={host}
                        organizationId={organizationId}
                        isThisComputer={host.id === thisHostId}
                        savedProfileByHarnessId={savedProfileByHarnessId}
                        onRefresh={onRefresh}
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
                            <Button variant="primary"
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
                <Button type="button" variant="quiet" size="sm" onClick={() => void copy()} className="gap-1.5">
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
                <Button variant="secondary" type="button" size="sm" onClick={onDone}>
                    I ran them
                </Button>
            </div>
        </div>
    );
}

function AgentHostCard({
    host,
    organizationId,
    isThisComputer,
    savedProfileByHarnessId,
    onRefresh,
}: {
    host: AgentHost;
    organizationId: string;
    isThisComputer: boolean;
    savedProfileByHarnessId: Map<string, AgentRuntimeProfileResponse>;
    onRefresh?: () => void;
}) {
    const harnesses = useAgentHostHarnesses(host.id);
    const revoke = useRevokeAgentHost();
    const [confirmDisconnect, setConfirmDisconnect] = useState(false);
    const activeRuns = host.capacity?.active_runs ?? 0;
    const maxRuns = host.capacity?.max_runs ?? null;
    const online = host.status === 'ONLINE';

    const disconnect = async () => {
        try {
            // Revoking is the same decision as Disconnect on the card, and has
            // to survive a navigation for the same reason: otherwise this
            // computer silently pairs itself back on the next page.
            if (isThisComputer) declineAutoConnect();
            await revoke.mutateAsync(host.id);
            setConfirmDisconnect(false);
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
                    <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-[var(--text-primary)]">{host.display_name}</span>
                        {isThisComputer ? <StatusBadge label="This computer" tone="muted" /> : null}
                    </div>
                    <div className="text-xs text-[var(--text-tertiary)]">
                        Agent Host {host.host_release} · {activeRuns}
                        {maxRuns === null ? '' : `/${maxRuns}`} running
                        {host.last_seen_at ? ` · seen ${new Date(host.last_seen_at).toLocaleTimeString()}` : ''}
                    </div>
                </div>
                <StatusBadge label={agentHostStatusLabel(host.status)} tone={online ? 'ok' : 'muted'} />
                <Button
                    type="button"
                    variant="quiet"
                    size="sm"
                    onClick={() => void harnesses.refetch()}
                    disabled={harnesses.isFetching}
                    aria-label={`Recheck ${host.display_name}`}
                >
                    <RefreshCw className={cn('size-4', harnesses.isFetching && 'lemma-spin')} />
                </Button>
                <Button
                    type="button"
                    variant="quiet"
                    size="sm"
                    onClick={() => setConfirmDisconnect(true)}
                    loading={revoke.isPending}
                    aria-label={`Disconnect ${host.display_name}`}
                >
                    <Trash2 className="size-4" />
                </Button>
                <DestructiveConfirmationDialog
                    open={confirmDisconnect}
                    onOpenChange={setConfirmDisconnect}
                    title={`Disconnect ${host.display_name}?`}
                    description="Its credential is revoked immediately and new runs stop."
                    resourceName={host.display_name}
                    confirmationText=""
                    consequences={[
                        'Coding agents added from this computer stop being available.',
                        'Pair the computer again to bring them back.',
                    ]}
                    confirmLabel="Disconnect"
                    pendingLabel="Disconnecting..."
                    isPending={revoke.isPending}
                    onConfirm={() => void disconnect()}
                />
            </div>
            <div className="flex flex-col gap-2 border-t border-[var(--border-subtle)] p-3">
                {harnesses.isLoading ? (
                    <p className="px-1 text-xs text-[var(--text-tertiary)]">Looking for agents on this computer…</p>
                ) : null}
                {(harnesses.data?.items ?? []).map((harness) => (
                    <AgentHostHarnessRow
                        key={harness.id}
                        harness={harness}
                        organizationId={organizationId}
                        hostOnline={online}
                        savedProfile={savedProfileByHarnessId.get(harness.id) ?? null}
                        onRefresh={onRefresh}
                    />
                ))}
                {!harnesses.isLoading && !(harnesses.data?.items.length ?? 0) ? (
                    <p className="px-1 text-xs text-[var(--text-tertiary)]">
                        No agents published yet. {isThisComputer
                            ? 'Use "Recheck agents" above to look again now.'
                            : 'That computer republishes what it finds every 15 minutes.'}
                    </p>
                ) : null}
            </div>
        </div>
    );
}

function AgentHostHarnessRow({
    harness,
    organizationId,
    hostOnline,
    savedProfile,
    onRefresh,
}: {
    harness: AgentHostHarness;
    organizationId: string;
    hostOnline: boolean;
    savedProfile: AgentRuntimeProfileResponse | null;
    onRefresh?: () => void;
}) {
    const [dialog, setDialog] = useState<HarnessDialogTarget | null>(null);
    const restore = useRestoreAgentRuntime();
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

    const archived = savedProfile ? isArchivedProfile(savedProfile) : false;

    const restoreSaved = async () => {
        if (!savedProfile) return;
        try {
            await restore.mutateAsync({ organizationId, profileId: savedProfile.id });
            toast.success(`${savedProfile.name} restored`);
            onRefresh?.();
        } catch (error) {
            toast.error(`Couldn't restore: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

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
                {savedProfile ? (
                    <StatusBadge
                        label={archived ? `Archived as ${savedProfile.name}` : `Added as ${savedProfile.name}`}
                        tone="muted"
                    />
                ) : null}
                <StatusBadge label={health.label} tone={usable ? 'ok' : 'muted'} />
            </div>
            {blockedReason ? <p className="mt-2 text-xs text-[var(--text-tertiary)]">{blockedReason}</p> : null}
            {/*
             * Offered only when the computer can actually take the profile.
             * Creating one binds it to a live harness — the backend reads the
             * host's config options to validate the selections — so offering
             * this against a sleeping laptop meant taking the user through the
             * whole dialog and then failing on save. Everything else on this
             * row still renders while offline; only creating is withheld.
             */}
            {!savedProfile && usable ? (
                <div className="mt-2">
                    <Button
                        type="button"
                        size="sm"
                        variant="quiet"
                        className="gap-1.5 px-2"
                        onClick={() => setDialog({ mode: 'create', harness })}
                    >
                        <Plus className="size-3.5" />
                        Add to chat models
                    </Button>
                </div>
            ) : null}
            {archived ? (
                <div className="mt-2">
                    <Button
                        type="button"
                        size="sm"
                        variant="quiet"
                        className="gap-1.5 px-2"
                        loading={restore.isPending}
                        loadingLabel="Restoring"
                        onClick={() => void restoreSaved()}
                    >
                        <RotateCcw className="size-3.5" />
                        Restore
                    </Button>
                </div>
            ) : null}
            {harness.stale_reason ? (
                <p className="mt-1 text-xs text-[var(--text-tertiary)]">{harness.stale_reason}</p>
            ) : null}

            <HarnessProfileDialog
                target={dialog}
                organizationId={organizationId}
                onClose={() => setDialog(null)}
                onSaved={onRefresh}
            />
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
