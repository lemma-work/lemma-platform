'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useCallback, useState } from 'react';
import { RuntimeProfileKind, RuntimeProfileScope } from 'lemma-sdk';
import type { AgentRuntimeProfileResponse } from 'lemma-sdk';
import { Cpu, Download, KeyRound, Pencil, Plus, RefreshCw, RotateCcw, Sparkles, TerminalSquare, Trash2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Switch, SwitchThumb, SwitchTrack } from '@/components/ui/switch';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { SettingsList, SettingsPanel, SettingsRow, SettingsStack } from '@/components/settings/settings-kit';
import { declineAutoConnect } from '@/lib/desktop/auto-connect';
import { useIsDesktopShell } from '@/lib/desktop/agent-host-bridge';
import {
    useAgentHostHarnesses,
    useAgentHostHarnessOwners,
    useAgentHosts,
    useArchiveAgentRuntime,
    useManagedAgentRuntimes,
    useRestoreAgentRuntime,
    useRevokeAgentHost,
    type AgentHost,
    type AgentHostHarness,
} from '@/lib/hooks/use-agent-runtime';
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
    harnessLogo,
    profileHarnessKey,
    runtimeAvailabilityLabel,
    type CustomProviderKind,
} from './agent-runtime-helpers';

/**
 * Ownership, but only when it is the exception.
 *
 * Workspace scope is the default every profile lands in, so labelling it put a
 * "Workspace" chip on every row — a column of identical badges that said
 * nothing and crowded out the one badge that does say something. Only a profile
 * that is *not* shared earns a mark.
 */
function scopeBadge(scope: RuntimeProfileScope): { label: string; tone: 'ok' | 'muted' } | null {
    return scope === RuntimeProfileScope.PERSONAL ? { label: 'Personal', tone: 'muted' } : null;
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
    const hosts = useAgentHosts();
    // A revoked host stays readable through the API for audit, but it can never
    // take work again, so it has no place in a "what can I use" list.
    const activeHosts = (hosts.data?.items ?? []).filter((host) => host.status !== 'REVOKED');
    const harnessOwners = useAgentHostHarnessOwners(activeHosts);
    const isRefreshing = managed.isFetching || hosts.isFetching;

    const providers = profiles.filter((profile) => profile.kind === RuntimeProfileKind.MODEL_PROVIDER);
    const codingAgents = profiles.filter((profile) => profile.kind === RuntimeProfileKind.HARNESS);

    const refreshAll = () => {
        void managed.refetch();
        void hosts.refetch();
        void onRefresh?.();
    };

    return (
        <SettingsStack>
            <div className="flex items-start justify-between gap-4">
                <p className="max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">
                    What this workspace can pick in a chat, and where it runs.
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

            <ModelsSection
                organizationId={organizationId}
                providers={providers}
                codingAgents={codingAgents}
                harnessOwners={harnessOwners}
                onRefresh={refreshAll}
            />

            <ComputersSection
                organizationId={organizationId}
                hosts={activeHosts}
                loadingHosts={hosts.isLoading}
                savedProfiles={codingAgents}
                onRefresh={refreshAll}
            />
        </SettingsStack>
    );
}

/**
 * Everything the model picker can offer, in one list.
 *
 * A key and a laptop are different things to *operate*, which is why the
 * computers have their own section below — but they are the same thing to
 * *choose*, and splitting the choice across two lists meant a fresh workspace
 * opened on a section whose entire content was "None yet, add one from a paired
 * computer below". A section that exists to point at another section is a
 * section the reader has to assemble themselves.
 */
function ModelsSection({
    organizationId,
    providers,
    codingAgents,
    harnessOwners,
    onRefresh,
}: {
    organizationId: string;
    providers: AgentRuntimeProfileResponse[];
    codingAgents: AgentRuntimeProfileResponse[];
    harnessOwners: Map<string, string>;
    onRefresh?: () => void;
}) {
    const [providerDialog, setProviderDialog] = useState<ProviderDialogTarget | null>(null);
    const [harnessDialog, setHarnessDialog] = useState<HarnessDialogTarget | null>(null);

    return (
        <SettingsPanel
            title="Models"
            description="Providers are shared with everyone here. Coding agents run on the computer they came from."
        >
            <div className="flex flex-col gap-3">
                <SettingsList>
                    {providers.map((profile) => (
                        <ProviderRow
                            key={profile.id}
                            profile={profile}
                            organizationId={organizationId}
                            onEdit={() => setProviderDialog({ mode: 'edit', profile })}
                            onRefresh={onRefresh}
                        />
                    ))}
                    {codingAgents.map((profile) => (
                        <CodingAgentRow
                            key={profile.id}
                            profile={profile}
                            organizationId={organizationId}
                            computer={
                                profile.harness_id ? harnessOwners.get(profile.harness_id) ?? null : null
                            }
                            onEdit={() => setHarnessDialog({ mode: 'edit', profile })}
                            onRefresh={onRefresh}
                        />
                    ))}
                </SettingsList>

                <div>
                    <ConnectProviderMenu onPick={setProviderDialog} />
                </div>
            </div>

            <ProviderProfileDialog
                target={providerDialog}
                organizationId={organizationId}
                onClose={() => setProviderDialog(null)}
                onSaved={onRefresh}
            />
            <HarnessProfileDialog
                target={harnessDialog}
                organizationId={organizationId}
                onClose={() => setHarnessDialog(null)}
                onSaved={onRefresh}
            />
        </SettingsPanel>
    );
}

// One button instead of eleven chips. The chips were a wall of equal-weight
// names that made choosing a provider look like the main thing this page is
// for, when for most workspaces the built-in models already answer it.
function ConnectProviderMenu({ onPick }: { onPick: (target: ProviderDialogTarget) => void }) {
    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button type="button" variant="secondary" size="sm" className="gap-1.5">
                    <Plus className="size-3.5" />
                    Connect a provider
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56">
                {PROVIDER_PRESETS.map((preset) => (
                    <DropdownMenuItem
                        key={preset.id}
                        onSelect={() =>
                            onPick({
                                mode: 'connect',
                                kind: preset.kind,
                                name: preset.name,
                                baseUrl: preset.baseUrl,
                            })
                        }
                    >
                        {preset.name}
                    </DropdownMenuItem>
                ))}
                <DropdownMenuSeparator />
                {CUSTOM_PROVIDER_OPTIONS.map((option) => (
                    <DropdownMenuItem
                        key={option.kind}
                        onSelect={() =>
                            onPick({
                                mode: 'connect',
                                kind: option.kind,
                                name: '',
                                baseUrl: option.defaultBaseUrl,
                            })
                        }
                    >
                        {option.kind === 'openai' ? 'Anything OpenAI-compatible' : 'Anything Anthropic-compatible'}
                    </DropdownMenuItem>
                ))}
            </DropdownMenuContent>
        </DropdownMenu>
    );
}

/**
 * One badge per row, answering one question: can I use this right now?
 *
 * The rows used to carry up to four — archived, scope, availability, built-in —
 * which mixed what a thing *is* with who *owns* it and whether it *works*, so
 * none of them read as status. Ownership only appears when it is the exception
 * (a personal profile in a shared workspace); everything else is folded into
 * this one label.
 */
function modelStatus(profile: AgentRuntimeProfileResponse): { label: string; tone: 'ok' | 'muted' } {
    if (isArchivedProfile(profile)) return { label: 'Archived', tone: 'muted' };
    if (profile.scope === RuntimeProfileScope.SYSTEM) return { label: 'Built in', tone: 'ok' };
    const availability = runtimeAvailabilityLabel(profile);
    if (availability) return { label: availability, tone: 'muted' };
    return { label: 'Ready', tone: 'ok' };
}

function ModelRow({
    icon,
    name,
    detail,
    profile,
    organizationId,
    onEdit,
    onRefresh,
}: {
    icon: React.ReactNode;
    name: string;
    detail: string;
    profile: AgentRuntimeProfileResponse;
    organizationId: string;
    onEdit: () => void;
    onRefresh?: () => void;
}) {
    const status = modelStatus(profile);
    const scope = scopeBadge(profile.scope);

    return (
        <SettingsRow>
            <div className="flex min-w-0 items-center gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[var(--surface-1)] text-[var(--text-secondary)]">
                    {icon}
                </span>
                <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-[var(--text-primary)]">{name}</div>
                    <div className="truncate text-xs text-[var(--text-tertiary)]">{detail}</div>
                </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
                {scope ? <StatusBadge label={scope.label} tone={scope.tone} /> : null}
                <StatusBadge label={status.label} tone={status.tone} />
                <ProfileRowActions
                    profile={profile}
                    organizationId={organizationId}
                    onEdit={onEdit}
                    onRefresh={onRefresh}
                />
            </div>
        </SettingsRow>
    );
}

function ProviderRow({
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
    const isSystem = profile.scope === RuntimeProfileScope.SYSTEM;
    const modelCount = profile.model_catalog?.length ?? 0;

    return (
        <ModelRow
            icon={
                isSystem ? (
                    <Sparkles className="size-4 text-[var(--delight)]" />
                ) : (
                    <KeyRound className="size-4" />
                )
            }
            name={profile.name}
            detail={`${isSystem ? 'Built in' : 'Your key'}${
                modelCount ? ` · ${modelCount} model${modelCount === 1 ? '' : 's'}` : ''
            }`}
            profile={profile}
            organizationId={organizationId}
            onEdit={onEdit}
            onRefresh={onRefresh}
        />
    );
}

function CodingAgentRow({
    profile,
    organizationId,
    computer,
    onEdit,
    onRefresh,
}: {
    profile: AgentRuntimeProfileResponse;
    organizationId: string;
    /** Null while the owning computer's harness list is still loading. */
    computer: string | null;
    onEdit: () => void;
    onRefresh?: () => void;
}) {
    const logo = harnessLogo(profileHarnessKey(profile));

    return (
        <ModelRow
            icon={
                logo ? (
                    <Image src={logo} alt="" width={18} height={18} className="size-4.5 object-contain" />
                ) : (
                    <TerminalSquare className="size-4" />
                )
            }
            name={profile.name}
            detail={`${computer ? `On ${computer}` : 'On a connected computer'} · ${
                profile.default_model_name ?? 'agent picks the model'
            }`}
            profile={profile}
            organizationId={organizationId}
            onEdit={onEdit}
            onRefresh={onRefresh}
        />
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


/**
 * The machines that run this workspace's coding agents.
 *
 * Not a peer of Models — it is where the coding agents in that list come from.
 * A computer holds a scoped, separately revocable secret and reaches Lemma over
 * outbound HTTPS, so nothing here needs an inbound port or the user's own
 * session on that machine.
 */
function ComputersSection({
    organizationId,
    hosts,
    loadingHosts,
    savedProfiles,
    onRefresh,
}: {
    organizationId: string;
    hosts: AgentHost[];
    loadingHosts: boolean;
    savedProfiles: AgentRuntimeProfileResponse[];
    onRefresh?: () => void;
}) {
    const isDesktop = useIsDesktopShell();
    const [connecting, setConnecting] = useState(false);
    // Which paired computer is the one the user is sitting at. Only the desktop
    // app can answer that; in a browser it stays null and the list is unchanged.
    const [thisHostId, setThisHostId] = useState<string | null>(null);
    const onHostIdChange = useCallback((hostId: string | null) => setThisHostId(hostId), []);

    // Harnesses already saved as runtime profiles, so a row can say "added"
    // instead of leaving the user guessing whether picking this agent in a chat
    // is possible yet. Built from the management listing rather than the
    // catalog: an archived profile is absent from the catalog, so the row would
    // offer "Add to models" again and then 409 on the unique-name index.
    const savedProfileByHarnessId = new Map<string, AgentRuntimeProfileResponse>();
    for (const profile of savedProfiles) {
        if (profile.harness_id) savedProfileByHarnessId.set(profile.harness_id, profile);
    }

    // In a browser there is no "this computer" to offer, so an empty section
    // would just be a heading. The app is the thing that makes this work, so
    // that is what the empty state asks for.
    const showGetTheApp = !isDesktop && !loadingHosts && hosts.length === 0;

    return (
        <SettingsPanel
            title="Computers"
            description="Each one runs Claude Code, Codex and OpenCode for this workspace. Their credentials stay on the machine."
        >
            <div className="flex flex-col gap-3">
                <ThisComputerCard onHostIdChange={onHostIdChange} onPaired={onRefresh} />

                {loadingHosts ? (
                    <p className="text-sm text-[var(--text-tertiary)]">Loading computers…</p>
                ) : null}

                {hosts.map((host) => (
                    <AgentHostCard
                        key={host.id}
                        host={host}
                        organizationId={organizationId}
                        isThisComputer={host.id === thisHostId}
                        savedProfileByHarnessId={savedProfileByHarnessId}
                        onRefresh={onRefresh}
                    />
                ))}

                {showGetTheApp ? <GetTheAppCard /> : null}

                <div>
                    <Button
                        type="button"
                        variant="quiet"
                        size="sm"
                        className="gap-1.5"
                        onClick={() => setConnecting(true)}
                    >
                        <Plus className="size-3.5" />
                        {hosts.length ? 'Connect another computer' : 'Connect a computer'}
                    </Button>
                </div>
            </div>

            <ConnectComputerDialog
                open={connecting}
                onClose={() => setConnecting(false)}
            />
        </SettingsPanel>
    );
}

/** The empty state in a plain browser: the app is what makes this work. */
function GetTheAppCard() {
    return (
        <div className="flex flex-wrap items-start gap-3 rounded-md border border-dashed border-[var(--border-strong)] p-4">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[var(--surface-1)]">
                <Cpu className="size-4 text-[var(--text-secondary)]" />
            </span>
            <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-[var(--text-primary)]">
                    Run agents on your own computer
                </div>
                <p className="mt-1 text-sm text-[var(--text-tertiary)]">
                    Claude Code, Codex and OpenCode already live on your machine. Install the Lemma app
                    there, sign in, and connect it in one click.
                </p>
            </div>
            <Button asChild variant="primary" size="sm" className="gap-1.5">
                <Link href="/download">
                    <Download className="size-3.5" />
                    Get the Lemma app
                </Link>
            </Button>
        </div>
    );
}

/**
 * Pairing a machine that is not this one.
 *
 * This used to be the page's opening move: a code box expanded before anything
 * was paired, three raw commands and a live single-use credential taking up
 * most of the viewport, while the one-click path in the app appeared nowhere.
 * It is the fallback, so it lives behind a button and says so — install the app
 * over there and it is one click; run these if that machine has no screen.
 */
function ConnectComputerDialog({
    open,
    onClose,
}: {
    open: boolean;
    onClose: () => void;
}) {
    // There used to be a second path here for a machine with no screen: mint a
    // single-use code, carry it over, and paste it into a CLI that downloaded
    // the Agent Host binary and registered it as an OS service. That channel is
    // gone — Desktop compiles and supervises the only copy of Agent Host — so a
    // computer with no screen has nothing to pair, and offering it a code would
    // hand out a credential nothing can spend.
    return (
        <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
            <DialogContent className="gap-5">
                <DialogHeader>
                    <DialogTitle>Connect a computer</DialogTitle>
                    <DialogDescription>
                        The app signs in as you and connects itself. There is no code to carry and
                        nothing to copy.
                    </DialogDescription>
                </DialogHeader>

                <ol className="flex flex-col gap-3">
                    {[
                        'Install the Lemma app on that computer.',
                        'Open it and sign in to this workspace.',
                        'Models → Computers → Connect this computer.',
                    ].map((step, index) => (
                        <li key={step} className="flex items-baseline gap-3">
                            <span className="min-w-5 font-mono text-xs text-[var(--text-tertiary)]">
                                {(index + 1).toString().padStart(2, '0')}
                            </span>
                            <span className="text-sm text-[var(--text-secondary)]">{step}</span>
                        </li>
                    ))}
                </ol>

                <DialogFooter>
                    <Button asChild variant="primary" className="gap-1.5">
                        <Link href="/download">
                            <Download className="size-3.5" />
                            Get the Lemma app
                        </Link>
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
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
                        Add to models
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
