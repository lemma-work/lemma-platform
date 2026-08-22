'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useCallback, useState, type ReactNode } from 'react';
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
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { EmptyState } from '@/components/shared/empty-state';
import { ListSkeleton } from '@/components/shared/loading';
import { agentHostBridge, useIsDesktopShell } from '@/lib/desktop/agent-host-bridge';
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
    agentHostStatusLabel,
    describeHarness,
    isArchivedProfile,
    isDiscoveringHarnesses,
    harnessLogo,
    profileHarnessKey,
    runtimeAvailabilityLabel,
    type CustomProviderKind,
} from './agent-runtime-helpers';

/**
 * Ownership, but only when it is the exception.
 *
 * Organization scope is the default every profile lands in, so labelling it put
 * an identical chip on every row — a column of badges that said nothing and
 * crowded out the one badge that does say something. Only a profile that is
 * *not* shared earns a mark, which is also the one a reader in a pod needs:
 * everything unmarked is picked from by every pod, this row is picked from by
 * you alone.
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

/**
 * A ledger, not a stack of forms.
 *
 * This read as four boxes and a floating paragraph to answer one question. It
 * now reads the way Access and Automation do, and for the same reason: a lead
 * paragraph, then bare rows. Nothing here is a card, because a card is a
 * promise that its contents belong together and are separate from what follows
 * — and "the models you can pick" and "the computers some of them run on" are
 * one page, read top to bottom, not two places to be switched between.
 */
export function ModelsSettings({
    organizationId,
    defaultRow,
    onRefresh,
}: {
    organizationId: string;
    /**
     * The caller's own "what do I use by default" control, rendered above the
     * list it chooses from.
     *
     * A slot rather than a prop pair because the decision belongs to whoever
     * owns it — a pod stores its default on the pod — while the list is
     * organization data this component already holds.
     */
    defaultRow?: ReactNode;
    /** Extra work to do on "Recheck" — the page's own catalog query, if it has one. */
    onRefresh?: () => void | Promise<void>;
}) {
    const [showArchived, setShowArchived] = useState(false);
    // Archived profiles come down with everything else and are split below, so
    // the toggle can name its own count instead of being a switch that might
    // reveal nothing.
    const managed = useManagedAgentRuntimes(organizationId, { includeArchived: true });
    const profiles = managed.data?.items ?? [];
    const hosts = useAgentHosts();
    // A revoked host stays readable through the API for audit, but it can never
    // take work again, so it has no place in a "what can I use" list.
    const activeHosts = (hosts.data?.items ?? []).filter((host) => host.status !== 'REVOKED');
    const harnessOwners = useAgentHostHarnessOwners(activeHosts);
    const isRefreshing = managed.isFetching || hosts.isFetching;

    const live = profiles.filter((profile) => !isArchivedProfile(profile));
    const archived = profiles.filter(isArchivedProfile);
    const providers = live.filter((profile) => profile.kind === RuntimeProfileKind.MODEL_PROVIDER);
    const codingAgents = live.filter((profile) => profile.kind === RuntimeProfileKind.HARNESS);

    const refreshAll = () => {
        void managed.refetch();
        void hosts.refetch();
        void onRefresh?.();
    };

    return (
        <div className="space-y-5">
            <p className="text-sm leading-6 text-[var(--text-secondary)]">
                Everything this pod can pick in a chat. The list is shared with every pod in the
                organization; coding agents run on the computer they came from.
            </p>

            {/* Recheck sits on the pod default's line rather than above the lead
                paragraph, where it belonged to nothing. Every control on this
                page now shares a right edge with the thing it acts on. */}
            {defaultRow ? (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-2.5">
                    <div className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-4 gap-y-2">
                        {defaultRow}
                    </div>
                    <Button
                        type="button"
                        variant="quiet"
                        size="sm"
                        onClick={refreshAll}
                        disabled={isRefreshing}
                        className="shrink-0 gap-1.5"
                    >
                        <RefreshCw className={cn('size-3.5', isRefreshing && 'lemma-spin')} />
                        Recheck
                    </Button>
                </div>
            ) : null}

            <ModelsSection
                organizationId={organizationId}
                providers={providers}
                codingAgents={codingAgents}
                archived={showArchived ? archived : []}
                archivedCount={archived.length}
                showArchived={showArchived}
                onToggleArchived={() => setShowArchived((current) => !current)}
                harnessOwners={harnessOwners}
                loading={managed.isLoading}
                onRefresh={refreshAll}
            />

            <ComputersSection
                organizationId={organizationId}
                hosts={activeHosts}
                loadingHosts={hosts.isLoading}
                savedProfiles={[...codingAgents, ...archived]}
                onRefresh={refreshAll}
            />
        </div>
    );
}

/**
 * A group heading with its own actions on the same line.
 *
 * Both groups use it, so "Connect a provider" and "Connect a computer" land on
 * the same right edge at the same height in their sections instead of one
 * dangling under a list and the other under a paragraph.
 */
function LedgerSectionHeader({
    title,
    hint,
    actions,
}: {
    title: string;
    hint?: string;
    actions?: ReactNode;
}) {
    return (
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
                <h3 className="type-eyebrow">{title}</h3>
                {hint ? <p className="text-xs text-[var(--text-tertiary)]">{hint}</p> : null}
            </div>
            {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
        </div>
    );
}

/**
 * Everything the model picker can offer, in one list.
 *
 * A key and a laptop are different things to *operate*, which is why the
 * computers keep their own group below — but they are the same thing to
 * *choose*, and splitting the choice across two lists meant a fresh
 * organization opened on a section whose entire content was "None yet, add one
 * from a paired computer below". A section that exists to point at another
 * section is a section the reader has to assemble themselves.
 */
function ModelsSection({
    organizationId,
    providers,
    codingAgents,
    archived,
    archivedCount,
    showArchived,
    onToggleArchived,
    harnessOwners,
    loading,
    onRefresh,
}: {
    organizationId: string;
    providers: AgentRuntimeProfileResponse[];
    codingAgents: AgentRuntimeProfileResponse[];
    archived: AgentRuntimeProfileResponse[];
    archivedCount: number;
    showArchived: boolean;
    onToggleArchived: () => void;
    harnessOwners: Map<string, string>;
    loading: boolean;
    onRefresh?: () => void;
}) {
    const [providerDialog, setProviderDialog] = useState<ProviderDialogTarget | null>(null);
    const [harnessDialog, setHarnessDialog] = useState<HarnessDialogTarget | null>(null);
    const rows = [...providers, ...codingAgents, ...archived];

    return (
        <section className="space-y-2">
            <LedgerSectionHeader
                title="Models"
                actions={(
                    <>
                        {/* Only when there is something behind it. A permanent
                            "Show archived" is an invitation to look at nothing. */}
                        {archivedCount > 0 ? (
                            <Button type="button" variant="quiet" size="sm" onClick={onToggleArchived}>
                                {showArchived ? 'Hide archived' : `Show archived (${archivedCount})`}
                            </Button>
                        ) : null}
                        <ConnectProviderMenu onPick={setProviderDialog} />
                    </>
                )}
            />
            {loading ? <ListSkeleton rows={3} /> : rows.length === 0 ? (
                <EmptyState
                    variant="region"
                    icon={<Sparkles className="h-5 w-5" />}
                    title="No models yet"
                    description="Connect a provider key, or pair a computer to bring the coding agents already installed on it."
                />
            ) : (
                <ul className="lemma-index-list">
                    {rows.map((profile) => (
                        profile.kind === RuntimeProfileKind.HARNESS ? (
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
                        ) : (
                            <ProviderRow
                                key={profile.id}
                                profile={profile}
                                organizationId={organizationId}
                                onEdit={() => setProviderDialog({ mode: 'edit', profile })}
                                onRefresh={onRefresh}
                            />
                        )
                    ))}
                </ul>
            )}

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
        </section>
    );
}

// One button instead of eleven chips. The chips were a wall of equal-weight
// names that made choosing a provider look like the main thing this page is
// for, when for most organizations the built-in models already answer it.
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
 * One status per row, answering one question: can I use this right now?
 *
 * The rows used to carry up to four badges — archived, scope, availability,
 * built-in — which mixed what a thing *is* with who *owns* it and whether it
 * *works*, so none of them read as status. Ownership only appears when it is
 * the exception (a personal profile in a shared organization); everything else
 * folds into this one label.
 */
function modelStatus(profile: AgentRuntimeProfileResponse): { label: string; tone: StatusTone } {
    if (isArchivedProfile(profile)) return { label: 'Archived', tone: 'muted' };
    if (profile.scope === RuntimeProfileScope.SYSTEM) return { label: 'Built in', tone: 'ok' };
    const availability = runtimeAvailabilityLabel(profile);
    if (availability) return { label: availability, tone: 'muted' };
    return { label: 'Ready', tone: 'ok' };
}

type StatusTone = 'ok' | 'warn' | 'muted';

/**
 * Status the way every other ledger in the pod states it: a coloured dot and a
 * word, at the right edge of the row.
 *
 * The pill this replaces was a filled shape on a line that already had one for
 * the icon, so a list of four models was eight filled shapes and two of them
 * were the same grey. Triggers, runs and members have all read as dot-plus-word
 * for a while; this page was the holdout.
 */
function StatusDot({ label, tone }: { label: string; tone: StatusTone }) {
    return (
        <span
            className={cn(
                'inline-flex shrink-0 items-center gap-1.5 text-xs font-medium',
                tone === 'ok'
                    ? 'text-[var(--state-success)]'
                    : tone === 'warn'
                        ? 'text-[var(--state-warning)]'
                        : 'text-[var(--text-tertiary)]',
            )}
        >
            <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
            {label}
        </span>
    );
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
    icon: ReactNode;
    name: string;
    /** Empty when there is nothing to add beyond the name. */
    detail: string;
    profile: AgentRuntimeProfileResponse;
    organizationId: string;
    onEdit: () => void;
    onRefresh?: () => void;
}) {
    const status = modelStatus(profile);
    const scope = scopeBadge(profile.scope);

    return (
        <li className="lemma-index-row group flex items-center gap-2.5">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-[var(--surface-1)] text-[var(--text-secondary)]">
                {icon}
            </span>
            {/* Name and detail on one line, not stacked. A two-line row for
                "Lemma / 4 models" is a paragraph where a sentence would do, and
                it is what made four models fill a screen. */}
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="truncate text-sm font-medium text-[var(--text-primary)]">{name}</span>
                {detail ? (
                    <span className="truncate text-xs text-[var(--text-tertiary)]">{detail}</span>
                ) : null}
                {scope ? <span className="chip chip-sm chip-muted">{scope.label}</span> : null}
            </div>
            <StatusDot label={status.label} tone={status.tone} />
            <ProfileRowActions
                profile={profile}
                organizationId={organizationId}
                onEdit={onEdit}
                onRefresh={onRefresh}
            />
        </li>
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
    // "Built in" is the row's status badge. Printing it here as well made every
    // built-in row say the same word twice, three inches apart.
    const detail = [
        isSystem ? null : 'Shared key',
        modelCount ? `${modelCount} model${modelCount === 1 ? '' : 's'}` : null,
    ].filter(Boolean).join(' · ');

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
            detail={detail}
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
            <ResourceActionsMenu
                ariaLabel={`Actions for ${profile.name}`}
                align="end"
                // Quiet until wanted, like every other ledger row in the pod.
                triggerClassName="h-7 w-7 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
            >
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
 * The machines that run the coding agents in the Models list.
 *
 * Not a peer of Models — it is where those rows come from — which is why it is
 * a view of the same ledger rather than a second card stacked under it. A
 * computer holds a scoped, separately revocable secret and reaches Lemma over
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
        <section className="space-y-2">
            <LedgerSectionHeader
                title="Computers"
                hint="Their credentials never leave the machine."
                actions={(
                    <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        className="gap-1.5"
                        onClick={() => setConnecting(true)}
                    >
                        <Plus className="size-3.5" />
                        {hosts.length ? 'Connect another' : 'Connect a computer'}
                    </Button>
                )}
            />

            <ThisComputerCard onHostIdChange={onHostIdChange} onPaired={onRefresh} />

            {loadingHosts ? <ListSkeleton rows={2} /> : null}

            {hosts.map((host) => (
                <AgentHostGroup
                    key={host.id}
                    host={host}
                    organizationId={organizationId}
                    isThisComputer={host.id === thisHostId}
                    savedProfileByHarnessId={savedProfileByHarnessId}
                    onRefresh={onRefresh}
                />
            ))}

            {showGetTheApp ? <GetTheAppCard /> : null}

            <ConnectComputerDialog
                open={connecting}
                onClose={() => setConnecting(false)}
            />
        </section>
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
                    there and sign in — it connects itself.
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
                        'Open it and sign in with your Lemma account.',
                        'It appears here on its own, with the agents it found.',
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

/**
 * One computer and the agents it published, as a group heading over its rows.
 *
 * This was a bordered card holding a header and a second bordered region of
 * filled sub-cards — three surfaces deep for one laptop with three agents on
 * it, inside a panel that was a fourth. The computer is a heading now, and its
 * agents are rows underneath, which is the same shape Access uses for a person
 * and their roles.
 */
function AgentHostGroup({
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
    // An empty list means "still looking" for as long as looking is plausible.
    const discovering = isDiscoveringHarnesses(host, harnesses.data?.items.length ?? 0);
    // Only this computer can be asked to look again from here: the bridge talks
    // to the Agent Host in this process, not to somebody else's laptop.
    const onRecheckThisComputer = useCallback(() => {
        void agentHostBridge.refresh().then(
            () => {
                toast.success('Rechecking the agents on this computer');
                // The host answers on locald's event stream, so the harness
                // list this returns was read before the re-probe finished.
                setTimeout(() => void harnesses.refetch(), 1200);
            },
            (error: unknown) => toast.error(error instanceof Error ? error.message : String(error)),
        );
    }, [harnesses]);
    const revoke = useRevokeAgentHost();
    const [confirmRemove, setConfirmRemove] = useState(false);
    const activeRuns = host.capacity?.active_runs ?? 0;
    const maxRuns = host.capacity?.max_runs ?? null;
    const online = host.status === 'ONLINE';
    const items = harnesses.data?.items ?? [];

    const remove = async () => {
        try {
            await revoke.mutateAsync(host.id);
            setConfirmRemove(false);
            toast.success(`${host.display_name} removed`);
        } catch (error) {
            toast.error(`Couldn't remove: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    return (
        <section>
            <div className="group flex items-center gap-2.5 px-1 py-1.5">
                <TerminalSquare className="size-4 shrink-0 text-[var(--text-tertiary)]" />
                <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                        {host.display_name}
                    </span>
                    {isThisComputer ? <span className="chip chip-sm chip-muted">This computer</span> : null}
                    <span className="truncate text-xs text-[var(--text-tertiary)]">
                        Agent Host {host.host_release} · {activeRuns}
                        {maxRuns === null ? '' : `/${maxRuns}`} running
                        {host.last_seen_at ? ` · seen ${new Date(host.last_seen_at).toLocaleTimeString()}` : ''}
                    </span>
                </div>
                <StatusDot label={agentHostStatusLabel(host.status)} tone={online ? 'ok' : 'muted'} />
                <Button
                    type="button"
                    variant="quiet"
                    size="sm"
                    className="h-7 w-7 shrink-0 p-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                    onClick={() => void harnesses.refetch()}
                    disabled={harnesses.isFetching}
                    aria-label={`Recheck ${host.display_name}`}
                >
                    <RefreshCw className={cn('size-4', harnesses.isFetching && 'lemma-spin')} />
                </Button>
                {/*
                  * Only for a machine you are *not* at. Removing this computer
                  * revokes a credential the app would mint again on the next
                  * page, so the button could only ever be honest with a flag
                  * remembering that you meant it — which is exactly the state
                  * this lifecycle no longer keeps. Revoking a remote machine
                  * sticks by construction: it is not there to re-pair itself.
                  */}
                {isThisComputer ? null : (
                    <>
                        <Button
                            type="button"
                            variant="quiet"
                            size="sm"
                            className="h-7 w-7 shrink-0 p-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                            onClick={() => setConfirmRemove(true)}
                            loading={revoke.isPending}
                            aria-label={`Remove ${host.display_name}`}
                        >
                            <Trash2 className="size-4" />
                        </Button>
                        <DestructiveConfirmationDialog
                            open={confirmRemove}
                            onOpenChange={setConfirmRemove}
                            title={`Remove ${host.display_name}?`}
                            description="Its credential is revoked immediately and new runs stop."
                            resourceName={host.display_name}
                            confirmationText=""
                            consequences={[
                                'Coding agents added from this computer stop being available.',
                                'Opening Lemma on that computer connects it again.',
                            ]}
                            confirmLabel="Remove"
                            pendingLabel="Removing..."
                            isPending={revoke.isPending}
                            onConfirm={() => void remove()}
                        />
                    </>
                )}
            </div>

            {/*
              * `isLoading` alone was the bug: it is only true for the very
              * first fetch, and that fetch returns an empty list straight
              * away because the computer has not published anything yet.
              * The page then stated "No agents published yet" as fact while
              * the host was still installing adapter packages, which takes
              * minutes on a machine's first pairing.
              */}
            {harnesses.isLoading || discovering ? (
                <p className="flex items-center gap-2 px-1 py-2 pl-8 text-xs text-[var(--text-tertiary)]">
                    <RefreshCw className="size-3 lemma-spin" />
                    Looking for agents on this computer…
                    {isThisComputer ? ' The first time takes a few seconds longer.' : null}
                </p>
            ) : items.length === 0 ? (
                <p className="px-1 py-2 pl-8 text-xs text-[var(--text-tertiary)]">
                    No agents published yet. {isThisComputer
                        ? 'Use the recheck button above to look again now.'
                        : 'That computer publishes what it finds as soon as it changes.'}
                </p>
            ) : (
                <ul className="lemma-index-list pl-7">
                    {items.map((harness) => (
                        <AgentHostHarnessRow
                            key={harness.id}
                            harness={harness}
                            organizationId={organizationId}
                            hostOnline={online}
                            savedProfile={savedProfileByHarnessId.get(harness.id) ?? null}
                            onRefresh={onRefresh}
                            onRecheck={isThisComputer ? onRecheckThisComputer : undefined}
                        />
                    ))}
                </ul>
            )}
        </section>
    );
}

/**
 * One agent on one computer, as a row under that computer's heading.
 *
 * Uses `describeHarness` rather than `HarnessRow`: onboarding meets this list
 * as a standalone card and wants the fuller treatment, while here the computer
 * directly above already carries the icon, the reachability and the actions.
 * What the harness *is* still comes from one place; only the layout differs.
 */
function AgentHostHarnessRow({
    harness,
    organizationId,
    hostOnline,
    savedProfile,
    onRefresh,
    onRecheck,
}: {
    harness: AgentHostHarness;
    organizationId: string;
    hostOnline: boolean;
    savedProfile: AgentRuntimeProfileResponse | null;
    onRefresh?: () => void;
    onRecheck?: () => void;
}) {
    const [dialog, setDialog] = useState<HarnessDialogTarget | null>(null);
    const restore = useRestoreAgentRuntime();
    const archived = savedProfile ? isArchivedProfile(savedProfile) : false;
    const { logo, facts, statusLabel, usable, blockedReason } = describeHarness(harness, { hostOnline });

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

    /*
     * "Add to models" is offered only when the computer can actually take the
     * profile. Creating one binds it to a live harness — the backend reads the
     * host's config options to validate the selections — so offering this
     * against a sleeping laptop meant taking the user through the whole dialog
     * and then failing on save. Everything else on the row still renders while
     * offline; only creating is withheld.
     */
    const action = archived ? (
        <Button
            type="button"
            size="sm"
            variant="quiet"
            className="shrink-0 gap-1.5 px-2"
            loading={restore.isPending}
            loadingLabel="Restoring"
            onClick={() => void restoreSaved()}
        >
            <RotateCcw className="size-3.5" />
            Restore
        </Button>
    ) : savedProfile || !usable ? null : (
        <Button
            type="button"
            size="sm"
            variant="quiet"
            className="shrink-0 gap-1.5 px-2"
            onClick={() => setDialog({ mode: 'create', harness })}
        >
            <Plus className="size-3.5" />
            Add to models
        </Button>
    );

    return (
        <li className="lemma-index-row group flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[var(--surface-1)]">
                {logo ? (
                    <Image src={logo} alt="" width={14} height={14} className="size-3.5 object-contain" />
                ) : (
                    <TerminalSquare className="size-3.5 text-[var(--text-tertiary)]" />
                )}
            </span>
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                    {harness.display_name}
                </span>
                {facts.length > 0 ? (
                    <span className="truncate text-xs text-[var(--text-tertiary)]">{facts.join(' · ')}</span>
                ) : null}
                {savedProfile ? (
                    <span className="chip chip-sm chip-muted">
                        {archived ? `Archived as ${savedProfile.name}` : `Added as ${savedProfile.name}`}
                    </span>
                ) : null}
            </div>
            {/*
              * Only stated when the computer itself is reachable — otherwise the
              * heading directly above already said it, and repeating it on every
              * agent it owns is the same sentence three times.
              */}
            {blockedReason ? (
                <span className="basis-full pl-8 text-xs text-[var(--text-tertiary)]">{blockedReason}</span>
            ) : null}
            {/*
              * The copy for AUTH_REQUIRED already says "then let Agent Host
              * re-probe", and there was nothing anywhere to press. Probing is
              * otherwise on a fifteen-minute timer, so someone who signed in did
              * so and then watched nothing happen.
              */}
            {onRecheck && harness.health === 'AUTH_REQUIRED' ? (
                <Button
                    type="button"
                    size="sm"
                    variant="quiet"
                    className="shrink-0 gap-1.5 px-2"
                    onClick={onRecheck}
                >
                    <RefreshCw className="size-3.5" />
                    I&apos;ve signed in — re-check
                </Button>
            ) : null}
            {action}
            {hostOnline ? <StatusDot label={statusLabel} tone={usable ? 'ok' : 'muted'} /> : null}

            <HarnessProfileDialog
                target={dialog}
                organizationId={organizationId}
                onClose={() => setDialog(null)}
                onSaved={onRefresh}
            />
        </li>
    );
}
