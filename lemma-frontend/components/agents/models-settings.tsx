'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useCallback, useState, type ReactNode } from 'react';
import { RuntimeProfileKind, RuntimeProfileScope } from 'lemma-sdk';
import type { AgentRuntimeProfileResponse } from 'lemma-sdk';
import { Cpu, Download, KeyRound, Pencil, Plus, RefreshCw, RotateCcw, Sparkles, TerminalSquare } from '@/components/ui/icons';
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
    DropdownMenuLabel,
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
 * One ledger, not two lists that look the same.
 *
 * This page used to state Models and Computers as peers, in one row grammar, so
 * eight rows read as eight peers — and a coding agent that had been added
 * appeared in both, under the same name, with the same logo, both saying
 * "Ready". Nothing on the page said they were one thing.
 *
 * They were never peers. A computer is where some of these models *come from*,
 * so it is a group heading inside the list rather than a second section under
 * it, and every model — bought key or local agent — is written down exactly
 * once, in the place it comes from.
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
    const [providerDialog, setProviderDialog] = useState<ProviderDialogTarget | null>(null);
    // A profile is edited through the form that made it. Collapsing the two row
    // types into one `LedgerRow` must not collapse this with them: a coding
    // agent's settings are its harness's config options, and opening a base-URL
    // and API-key form over one edits fields it does not have.
    const [harnessDialog, setHarnessDialog] = useState<HarnessDialogTarget | null>(null);
    const [connectingComputer, setConnectingComputer] = useState(false);
    // Which paired computer is the one the user is sitting at. Only the desktop
    // app can answer that; in a browser it stays null and the list is unchanged.
    const [thisHostId, setThisHostId] = useState<string | null>(null);
    const onHostIdChange = useCallback((hostId: string | null) => setThisHostId(hostId), []);

    // Archived profiles come down with everything else and are split below, so
    // the toggle can name its own count instead of being a switch that might
    // reveal nothing.
    const managed = useManagedAgentRuntimes(organizationId, { includeArchived: true });
    const profiles = managed.data?.items ?? [];
    const hosts = useAgentHosts();
    // A revoked host stays readable through the API for audit, but it can never
    // take work again, so it has no place in a "what can I use" list.
    const activeHosts = (hosts.data?.items ?? []).filter((host) => host.status !== 'REVOKED');
    const { owners: harnessOwners, isPending: ownersPending } = useAgentHostHarnessOwners(activeHosts);
    const isDesktop = useIsDesktopShell();
    const isRefreshing = managed.isFetching || hosts.isFetching;

    const providers = profiles.filter((profile) => profile.kind === RuntimeProfileKind.MODEL_PROVIDER);
    const harnessProfiles = profiles.filter((profile) => profile.kind === RuntimeProfileKind.HARNESS);

    // A saved coding agent is drawn under the computer that published its
    // harness. What is left over is a profile whose computer is not in this
    // list — unpaired, or still loading — and it has to keep a row of its own
    // or it would simply vanish from a page that claims to list everything.
    //
    // Held back until the per-host queries settle: every harness looks detached
    // for the first frame, and rows that appear at the top and then jump into a
    // group below are the same disorientation this page was rebuilt to remove.
    const detached = ownersPending
        ? []
        : harnessProfiles.filter((profile) => !profile.harness_id || !harnessOwners.has(profile.harness_id));

    // Only the rows the toggle actually hides. A harness that is archived is
    // still drawn under its computer either way — the machine has it installed
    // whatever Lemma thinks of the profile — so counting it here would promise
    // more than the toggle reveals.
    const hidden = [...providers, ...detached].filter(isArchivedProfile);
    const rows = [...providers, ...detached].filter(
        (profile) => showArchived || !isArchivedProfile(profile),
    );

    const refreshAll = () => {
        void managed.refetch();
        void hosts.refetch();
        void onRefresh?.();
    };

    // In a browser there is no "this computer" to offer, so the page asks for
    // the app instead — but only once there is nothing else here to read.
    const showGetTheApp = !isDesktop && !hosts.isLoading && activeHosts.length === 0;
    const isEmpty = !managed.isLoading && rows.length === 0 && activeHosts.length === 0;

    return (
        <div className="space-y-5">
            <p className="text-sm leading-6 text-[var(--text-secondary)]">
                Everything this pod can pick in a chat. The list is shared with every pod in the
                organization; coding agents run on the computer they came from, and that computer&apos;s
                credentials never leave it.
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

            <section className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <h3 className="type-eyebrow">Models</h3>
                    <div className="flex shrink-0 items-center gap-1">
                        {/* Only when there is something behind it. A permanent
                            "Show archived" is an invitation to look at nothing. */}
                        {hidden.length > 0 ? (
                            <Button
                                type="button"
                                variant="quiet"
                                size="sm"
                                onClick={() => setShowArchived((current) => !current)}
                            >
                                {showArchived ? 'Hide archived' : `Show archived (${hidden.length})`}
                            </Button>
                        ) : null}
                        <ConnectMenu
                            onPickProvider={setProviderDialog}
                            onPickComputer={() => setConnectingComputer(true)}
                        />
                    </div>
                </div>

                {managed.isLoading ? <ListSkeleton rows={3} /> : isEmpty ? (
                    <EmptyState
                        variant="region"
                        icon={<Sparkles className="h-5 w-5" />}
                        title="No models yet"
                        description="Connect a provider key, or pair a computer to bring the coding agents already installed on it."
                    />
                ) : (
                    /*
                     * One bordered surface for the whole ledger.
                     *
                     * The rows used to float on the page under a hairline that
                     * stopped at 68% of the row and pointed at nothing, which is
                     * what read as unfinished. A panel gives the list an edge to
                     * meet, lets the dividers run the full width, and — because
                     * the computers are inside it rather than under it — draws
                     * the one claim this page is making: these are all one list.
                     */
                    <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)]">
                        {rows.length > 0 ? (
                            <ul className="lemma-index-list lemma-panel-list">
                                {rows.map((profile) => (
                                    <ProfileRow
                                        key={profile.id}
                                        profile={profile}
                                        organizationId={organizationId}
                                        onEdit={() => (
                                            profile.kind === RuntimeProfileKind.HARNESS
                                                ? setHarnessDialog({ mode: 'edit', profile })
                                                : setProviderDialog({ mode: 'edit', profile })
                                        )}
                                        onRefresh={refreshAll}
                                    />
                                ))}
                            </ul>
                        ) : null}

                        {hosts.isLoading ? (
                            <div className="px-3"><ListSkeleton rows={2} /></div>
                        ) : null}

                        {activeHosts.map((host, index) => (
                            <ComputerGroup
                                key={host.id}
                                host={host}
                                organizationId={organizationId}
                                isThisComputer={host.id === thisHostId}
                                savedProfiles={harnessProfiles}
                                // The band draws the rule that separates it from
                                // whatever is above. When it *is* the top of the
                                // panel there is nothing above but the panel's
                                // own border, and two lines a pixel apart read
                                // as one thick one.
                                isPanelTop={rows.length === 0 && index === 0}
                                onRefresh={refreshAll}
                            />
                        ))}
                    </div>
                )}

                {/* Reporting on the Agent Host process running beside this tab —
                    troubleshooting, not choosing — so it sits under the list
                    rather than interrupting it. Mounted on every desktop render
                    regardless: it is what pairs this machine in the first place,
                    and what tells the groups above which one it is. */}
                <ThisComputerCard onHostIdChange={onHostIdChange} onPaired={refreshAll} />

                {showGetTheApp ? <GetTheAppCard /> : null}
            </section>

            <ProviderProfileDialog
                target={providerDialog}
                organizationId={organizationId}
                onClose={() => setProviderDialog(null)}
                onSaved={refreshAll}
            />
            <HarnessProfileDialog
                target={harnessDialog}
                organizationId={organizationId}
                onClose={() => setHarnessDialog(null)}
                onSaved={refreshAll}
            />
            <ConnectComputerDialog
                open={connectingComputer}
                onClose={() => setConnectingComputer(false)}
            />
        </div>
    );
}

/**
 * One button for everything this list can gain.
 *
 * There were two, on two section headers, and which one you wanted depended on
 * knowing that a coding agent arrives through a machine rather than a key —
 * which is the thing the page is supposed to be teaching, not asking. Adding
 * anything starts in the same place now, and the menu draws the distinction.
 */
function ConnectMenu({
    onPickProvider,
    onPickComputer,
}: {
    onPickProvider: (target: ProviderDialogTarget) => void;
    onPickComputer: () => void;
}) {
    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button type="button" variant="secondary" size="sm" className="gap-1.5">
                    <Plus className="size-3.5" />
                    Connect
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>A provider key</DropdownMenuLabel>
                {PROVIDER_PRESETS.map((preset) => (
                    <DropdownMenuItem
                        key={preset.id}
                        onSelect={() =>
                            onPickProvider({
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
                {CUSTOM_PROVIDER_OPTIONS.map((option) => (
                    <DropdownMenuItem
                        key={option.kind}
                        onSelect={() =>
                            onPickProvider({
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
                <DropdownMenuSeparator />
                <DropdownMenuLabel>A computer</DropdownMenuLabel>
                <DropdownMenuItem onSelect={onPickComputer}>
                    Bring its coding agents…
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}

type StatusTone = 'ok' | 'warn' | 'muted';

/**
 * Can a chat pick this right now — asked of every row, answered on every row.
 *
 * The old column said "Ready" five times on a page of eight rows and meant
 * three different things by it: a key that works, a saved profile, an installed
 * agent nobody had added. The defect there was that one word covered three
 * questions, not that the column was answered — so this states one question,
 * and every row answers it in the same vocabulary.
 *
 * A row that leaves it blank is worse than a repetitive one. "Is Claude Code
 * available?" is the question the page exists to answer, and inferring yes from
 * the absence of an Add button is not answering it.
 */
function profileStatus(profile: AgentRuntimeProfileResponse): { label: string; tone: StatusTone } {
    if (isArchivedProfile(profile)) return { label: 'Archived', tone: 'muted' };
    const availability = runtimeAvailabilityLabel(profile);
    if (availability) return { label: availability, tone: 'warn' };
    return { label: 'Available', tone: 'ok' };
}

/**
 * Status the way every other ledger in the pod states it: a coloured dot and a
 * word, at the right edge of the row.
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

/**
 * Every row on this page, drawn once.
 *
 * A bought key, a coding agent on a laptop and a profile whose machine is gone
 * are the same object to a reader — a thing a chat can pick — so they are the
 * same row, and only what fills the slots differs. The trailing menu keeps its
 * 28px whether or not there is a menu in it, which is the whole of why the
 * status column used to be ragged: rows with a menu pushed their status inward
 * and rows without it did not, so no two words in the column shared an edge.
 */
function LedgerRow({
    icon,
    name,
    detail,
    chip,
    /** A fuller sentence about why this row cannot be used, under the name. */
    note,
    status,
    /** The row's own affordance — "Add", "Restore" — left of the menu slot. */
    action,
    menu,
    dimmed,
}: {
    icon: ReactNode;
    name: string;
    detail?: string;
    chip?: ReactNode;
    note?: string | null;
    status?: { label: string; tone: StatusTone } | null;
    action?: ReactNode;
    menu?: ReactNode;
    dimmed?: boolean;
}) {
    return (
        <li className={cn('lemma-index-row group flex items-center gap-2.5', dimmed && 'opacity-60')}>
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)] text-[var(--text-secondary)]">
                {icon}
            </span>
            {/* Name and detail on one line, not stacked. A two-line row for
                "Lemma / 6 models" is a paragraph where a sentence would do. The
                note is the exception, and it is the reason Cursor's failure used
                to break the grid: as a full-width line inside the row's own flex
                it wrapped the status down to the left margin. */}
            <div className="min-w-0 flex-1">
                <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className="truncate text-sm font-medium text-[var(--text-primary)]">{name}</span>
                    {detail ? (
                        <span className="truncate text-xs text-[var(--text-tertiary)]">{detail}</span>
                    ) : null}
                    {chip}
                </div>
                {note ? (
                    <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">{note}</p>
                ) : null}
            </div>
            <div className="flex shrink-0 items-center justify-end gap-1">
                {action}
                {status ? <StatusDot label={status.label} tone={status.tone} /> : null}
                <span className="flex size-7 shrink-0 items-center justify-center">{menu}</span>
            </div>
        </li>
    );
}

/**
 * A saved profile with no live harness behind it: a bought provider key, or a
 * coding agent whose computer is not in the list.
 */
function ProfileRow({
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
    const isHarness = profile.kind === RuntimeProfileKind.HARNESS;
    const logo = isHarness ? harnessLogo(profileHarnessKey(profile)) : undefined;
    const modelCount = profile.model_catalog?.length ?? 0;
    const scope = scopeBadge(profile.scope);

    // "Built in" is what this row *is*, not how it is doing — a fact that never
    // changes, printed once, next to the other facts. It spent this page's
    // whole history in the status column saying the same word forever.
    const detail = [
        isSystem ? 'Built in' : isHarness ? null : 'Shared key',
        modelCount ? `${modelCount} model${modelCount === 1 ? '' : 's'}` : null,
    ].filter(Boolean).join(' · ');

    const status = profileStatus(profile)
        ?? (isHarness ? { label: 'Computer not connected', tone: 'muted' as StatusTone } : null);

    return (
        <LedgerRow
            icon={
                logo ? (
                    <Image src={logo} alt="" width={18} height={18} className="size-4.5 object-contain" />
                ) : isSystem ? (
                    <Sparkles className="size-4 text-[var(--delight)]" />
                ) : isHarness ? (
                    <TerminalSquare className="size-4" />
                ) : (
                    <KeyRound className="size-4" />
                )
            }
            name={profile.name}
            detail={detail}
            chip={scope ? <span className="chip chip-sm chip-muted">{scope.label}</span> : null}
            status={status}
            menu={(
                <ProfileActionsMenu
                    profile={profile}
                    organizationId={organizationId}
                    onEdit={onEdit}
                    onRefresh={onRefresh}
                />
            )}
        />
    );
}

/**
 * Edit / archive / restore for one saved profile. SYSTEM-scope profiles are
 * Lemma's own built-ins — there is nothing here a workspace may change, so they
 * get no menu at all rather than a menu of disabled items.
 */
function ProfileActionsMenu({
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
 * It is the fallback, so it lives behind a menu item and says so — install the
 * app over there and it is one click.
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
 * One computer, as a heading over the models it brings.
 *
 * Not a card, not a section, and not indented: its agents are rows in the same
 * ledger as the provider keys above, sharing one left edge, one separator
 * origin and one right edge. A hairline and a smaller eyebrow are the whole of
 * what says "these came from here" — which is all the difference there is.
 */
function ComputerGroup({
    host,
    organizationId,
    isThisComputer,
    savedProfiles,
    isPanelTop,
    onRefresh,
}: {
    host: AgentHost;
    organizationId: string;
    isThisComputer: boolean;
    /** Every saved harness profile, live and archived, keyed off below. */
    savedProfiles: AgentRuntimeProfileResponse[];
    /** Whether this band is the first thing inside the panel. */
    isPanelTop?: boolean;
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

    // Profiles already created from this computer's harnesses, so a row can say
    // what it is in the picker instead of leaving the user guessing. Built from
    // the management listing rather than the catalog: an archived profile is
    // absent from the catalog, so the row would offer "Add" again and then 409
    // on the unique-name index.
    const savedByHarnessId = new Map<string, AgentRuntimeProfileResponse>();
    for (const profile of savedProfiles) {
        if (profile.harness_id) savedByHarnessId.set(profile.harness_id, profile);
    }

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
            <div
                className={cn(
                    'group flex items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--surface-2)] px-3 py-2',
                    isPanelTop ? null : 'border-t',
                )}
            >
                <TerminalSquare className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
                <h4 className="type-eyebrow-sm truncate">{host.display_name}</h4>
                {isThisComputer ? <span className="chip chip-sm chip-muted">This computer</span> : null}
                <span className="min-w-0 flex-1 truncate text-xs text-[var(--text-tertiary)]">
                    Agent Host {host.host_release} · {activeRuns}
                    {maxRuns === null ? '' : `/${maxRuns}`} running
                    {/* When it is online, "Online" is the last-seen. Printing a
                        second-precision clock beside it said the same thing
                        twice and put a number on the page that changes on its
                        own. It is worth reading only once it has stopped. */}
                    {!online && host.last_seen_at
                        ? ` · last seen ${new Date(host.last_seen_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
                        : ''}
                </span>
                {/* A computer's own reachability is the first half of "can I use
                    the agents on it", so it is stated the same way its agents
                    are — and from the same right edge, which is why the two
                    hover buttons that used to sit here are one menu now. */}
                <StatusDot
                    label={agentHostStatusLabel(host.status)}
                    tone={online ? 'ok' : 'muted'}
                />
                <ResourceActionsMenu
                    ariaLabel={`Actions for ${host.display_name}`}
                    align="end"
                    triggerClassName="h-7 w-7 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                >
                    <DropdownMenuItem
                        onSelect={(event) => {
                            event.preventDefault();
                            void harnesses.refetch();
                        }}
                    >
                        <RefreshCw className={cn('mr-2 h-4 w-4', harnesses.isFetching && 'lemma-spin')} />
                        Look for agents again
                    </DropdownMenuItem>
                    {/*
                      * Removing is only for a machine you are *not* at. Removing
                      * this computer revokes a credential the app would mint
                      * again on the next page, so the item could only ever be
                      * honest with a flag remembering that you meant it — which
                      * is exactly the state this lifecycle no longer keeps.
                      * Revoking a remote machine sticks by construction: it is
                      * not there to re-pair itself.
                      */}
                    {isThisComputer ? null : (
                        <DestructiveResourceActionItem onSelect={() => setConfirmRemove(true)}>
                            Remove
                        </DestructiveResourceActionItem>
                    )}
                </ResourceActionsMenu>
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
                <p className="flex items-center gap-2 px-3 py-2.5 text-xs text-[var(--text-tertiary)]">
                    <RefreshCw className="size-3 lemma-spin" />
                    Looking for agents on this computer…
                    {isThisComputer ? ' The first time takes a few seconds longer.' : null}
                </p>
            ) : items.length === 0 ? (
                <p className="px-3 py-2.5 text-xs text-[var(--text-tertiary)]">
                    No agents published yet. {isThisComputer
                        ? 'Use the recheck button above to look again now.'
                        : 'That computer publishes what it finds as soon as it changes.'}
                </p>
            ) : (
                <ul className="lemma-index-list lemma-panel-list">
                    {items.map((harness) => (
                        <ComputerAgentRow
                            key={harness.id}
                            harness={harness}
                            organizationId={organizationId}
                            hostOnline={online}
                            savedProfile={savedByHarnessId.get(harness.id) ?? null}
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
 * One coding agent on one computer — and, if it has been added, the profile it
 * is in the picker as. One row, because they are one thing.
 *
 * They used to be two: this row, and a second row for the saved profile in a
 * separate Models list above. Same name, same logo, both reading "Ready",
 * nothing connecting them. Whether a chat can pick this agent is a fact *about
 * the agent*, not a second object, so it is drawn here — as the presence or
 * absence of an Add.
 */
function ComputerAgentRow({
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
    const added = Boolean(savedProfile) && !archived;
    const { logo, modelCount, statusLabel, usable, blockedReason } = describeHarness(harness, { hostOnline });
    const scope = savedProfile ? scopeBadge(savedProfile.scope) : null;

    // The name a chat will offer, when there is one — that is what this row is
    // for. The harness keeps its own name as a fact whenever the two differ, so
    // renaming a profile never orphans the thing it was made from.
    const name = savedProfile?.name ?? harness.display_name;
    const detail = [
        savedProfile && savedProfile.name !== harness.display_name ? harness.display_name : null,
        modelCount ? `${modelCount} model${modelCount === 1 ? '' : 's'}` : null,
    ].filter(Boolean).join(' · ');

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
     * "Add" is offered only when the computer can actually take the profile.
     * Creating one binds it to a live harness — the backend reads the host's
     * config options to validate the selections — so offering this against a
     * sleeping laptop meant taking the user through the whole dialog and then
     * failing on save. Everything else on the row still renders while offline;
     * only creating is withheld.
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
    ) : added || !usable ? null : (
        <Button
            type="button"
            size="sm"
            variant="quiet"
            className="shrink-0 gap-1.5 px-2"
            onClick={() => setDialog({ mode: 'create', harness })}
        >
            <Plus className="size-3.5" />
            Add
        </Button>
    );

    /*
     * The same question the provider rows answer, in the same words. "Available"
     * means a chat can pick this now; "Not added" means the computer has it and
     * Lemma does not yet. The Add button beside "Not added" is the verb for that
     * state, not a repeat of it — the column is what makes the page scannable,
     * and an agent whose row went blank is exactly what could not be read.
     *
     * "Setting up" is the one non-ready state that is not a fault: the computer
     * is mid-probe and will finish on its own, so it stays grey while a genuine
     * failure — signed out, unsupported, could not start — is warned about.
     */
    const status: { label: string; tone: StatusTone } = archived
        ? { label: 'Archived', tone: 'muted' }
        : !hostOnline
            ? { label: 'Computer offline', tone: 'muted' }
            : !usable
                ? { label: statusLabel, tone: harness.health === 'INSTALLING' ? 'muted' : 'warn' }
                : added
                    ? { label: 'Available', tone: 'ok' }
                    : { label: 'Not added', tone: 'muted' };

    return (
        <>
            <LedgerRow
                icon={
                    logo ? (
                        <Image src={logo} alt="" width={18} height={18} className="size-4.5 object-contain" />
                    ) : (
                        <TerminalSquare className="size-4" />
                    )
                }
                name={name}
                detail={detail}
                chip={scope ? <span className="chip chip-sm chip-muted">{scope.label}</span> : null}
                // Stated only when the computer itself is reachable — otherwise
                // the heading directly above already said it, and repeating it
                // under every agent it owns is the same sentence three times.
                note={blockedReason}
                status={status}
                dimmed={!hostOnline}
                action={(
                    <>
                        {/*
                          * The copy for AUTH_REQUIRED already says "then let
                          * Agent Host re-probe", and there was nothing anywhere
                          * to press. Probing is otherwise on a fifteen-minute
                          * timer, so someone who signed in did so and then
                          * watched nothing happen.
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
                    </>
                )}
                menu={savedProfile ? (
                    <ProfileActionsMenu
                        profile={savedProfile}
                        organizationId={organizationId}
                        onEdit={() => setDialog({ mode: 'edit', profile: savedProfile })}
                        onRefresh={onRefresh}
                    />
                ) : null}
            />

            <HarnessProfileDialog
                target={dialog}
                organizationId={organizationId}
                onClose={() => setDialog(null)}
                onSaved={onRefresh}
            />
        </>
    );
}
