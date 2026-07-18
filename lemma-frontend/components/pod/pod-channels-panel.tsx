'use client';

import { useMemo, useState, type ComponentType } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import {
    Bot,
    Check,
    CheckCircle2,
    Copy,
    ExternalLink,
    Inbox,
    Loader2,
    Mail,
    MessageCircle,
    MessagesSquare,
    Plug,
    Plus,
    RefreshCw,
    Send,
    ShieldCheck,
    Smartphone,
    Trash2,
} from '@/components/ui/icons';
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
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch, SwitchThumb, SwitchTrack } from '@/components/ui/switch';
import { useAssistants } from '@/lib/hooks/use-assistants';
import { useAccounts } from '@/lib/hooks/use-connectors';
import { usePod } from '@/lib/hooks/use-pods';
import {
    type SurfacePlatformValue,
    useCreatePodSurface,
    useDeletePodSurface,
    usePodSurfaces,
    useSendSurfaceMessage,
    useSurfaceChannels,
    useSurfaceSetup,
    useTogglePodSurface,
    useUpdatePodSurface,
} from '@/lib/hooks/use-pod-surfaces';
import { usePodMembers } from '@/lib/hooks/use-pod-members';
import { Textarea } from '@/components/ui/textarea';
import type { Account, AssistantSurface } from '@/lib/types';
import { playSoundFeedback } from '@/lib/feedback/sound-feedback';
import type {
    SurfaceBehaviorConfigInput,
    SurfaceCredentialMode,
    SurfacePlatform,
    SurfaceSetupAction,
    SurfaceSetupActionField,
} from 'lemma-sdk';
import { cn } from '@/lib/utils';

type IdentityMode = 'BUILT_IN' | 'CONNECTED';
type SurfaceTone = 'success' | 'warning' | 'muted' | 'danger' | 'info';
type SurfacePanelMode = 'index' | 'create';
type ChannelDraft = { channel_id: string; channel_name: string; agent_name: string | null };
// The config dialog targets either an existing surface (addressed by its
// pod-unique name) or a brand-new one for a platform. A platform can hold
// several surfaces, so edits are keyed by name rather than by platform.
type SurfaceEditTarget =
    | { mode: 'create'; platform: SurfacePlatformValue }
    | { mode: 'edit'; platform: SurfacePlatformValue; surfaceName: string };
type SurfaceSendTarget = { surfaceName: string; label: string };
type AvailableChannel = { id: string; name?: string | null; is_member?: boolean | null };

type SurfaceDefinition = {
    platform: SurfacePlatformValue;
    label: string;
    promise: string;
    accountLabel: string;
    targetLabel: string;
    icon: ComponentType<{ className?: string }>;
    logoSrc?: string;
    builtInIdentity?: boolean;
};

const SURFACE_DEFINITIONS: SurfaceDefinition[] = [
    {
        platform: 'SLACK',
        label: 'Slack',
        promise: 'Let this pod answer in Slack DMs or one channel.',
        accountLabel: 'Slack workspace account',
        targetLabel: 'Slack channel ID',
        icon: MessagesSquare,
        logoSrc: '/surfaces/slack.png',
    },
    {
        platform: 'TEAMS',
        label: 'Teams',
        promise: 'Let this pod answer in Teams chats or one channel.',
        accountLabel: 'Microsoft Teams account',
        targetLabel: 'Teams channel ID',
        icon: MessageCircle,
        logoSrc: '/surfaces/teams.png',
    },
    {
        platform: 'GMAIL',
        label: 'Gmail',
        promise: 'Route a mailbox into this pod as email work.',
        accountLabel: 'Gmail account',
        targetLabel: 'Mailbox',
        icon: Mail,
        logoSrc: '/surfaces/gmail.png',
    },
    {
        platform: 'OUTLOOK',
        label: 'Outlook',
        promise: 'Route a Microsoft mailbox into this pod.',
        accountLabel: 'Outlook account',
        targetLabel: 'Mailbox',
        icon: Inbox,
        logoSrc: '/surfaces/outlook.png',
    },
    {
        platform: 'RESEND',
        label: 'Resend',
        promise: 'Send and receive email through Lemma’s managed Resend address — no mailbox to connect.',
        accountLabel: 'Resend account',
        targetLabel: 'Mailbox',
        icon: Mail,
    },
    {
        platform: 'TELEGRAM',
        label: 'Telegram',
        promise: 'Make this pod reachable from a Telegram bot.',
        accountLabel: 'Telegram bot account',
        targetLabel: 'Direct message',
        icon: Send,
        logoSrc: '/surfaces/telegram.png',
        builtInIdentity: true,
    },
    {
        platform: 'WHATSAPP',
        label: 'WhatsApp',
        promise: 'Make this pod reachable from WhatsApp.',
        accountLabel: 'WhatsApp account',
        targetLabel: 'Direct message',
        icon: Smartphone,
        logoSrc: '/surfaces/whatsapp.png',
        builtInIdentity: true,
    },
];

const DEFAULT_DM_RESET_HOURS = 24;
const DEFAULT_AGENT_VALUE = '__pod_default_agent__';

export function PodSurfacesPanel({
    podId,
    showHeading = true,
}: {
    podId: string;
    mode?: SurfacePanelMode;
    showHeading?: boolean;
}) {
    const { data: surfaces = [], isLoading: isLoadingSurfaces, refetch, isFetching } = usePodSurfaces(podId);
    const { data: assistantsData, isLoading: isLoadingAssistants } = useAssistants(podId);
    const { data: pod } = usePod(podId);
    const { data: accounts = [], isLoading: isLoadingAccounts } = useAccounts({ organizationId: pod?.organization_id, limit: 200 });
    const { mutate: toggleSurface, isPending: isToggling } = useTogglePodSurface();
    const { mutate: createSurface, isPending: isCreating } = useCreatePodSurface();
    const { mutate: updateSurface, isPending: isUpdating } = useUpdatePodSurface();
    const { mutate: deleteSurface, isPending: isDeleting } = useDeletePodSurface();

    const [editTarget, setEditTarget] = useState<SurfaceEditTarget | null>(null);
    const [sendTarget, setSendTarget] = useState<SurfaceSendTarget | null>(null);
    const [draftName, setDraftName] = useState('');
    const [draftAgentName, setDraftAgentName] = useState(DEFAULT_AGENT_VALUE);
    const [draftAccountId, setDraftAccountId] = useState('');
    const [draftIdentityMode, setDraftIdentityMode] = useState<IdentityMode>('BUILT_IN');
    const [draftChannels, setDraftChannels] = useState<ChannelDraft[]>([]);
    const [draftAllowedDomains, setDraftAllowedDomains] = useState('');
    const [draftAllowedEmails, setDraftAllowedEmails] = useState('');
    const [draftAllowSend, setDraftAllowSend] = useState(false);

    const assistants = assistantsData?.items ?? [];
    const isLoading = isLoadingSurfaces || isLoadingAssistants || isLoadingAccounts;
    const surfacesByPlatform = useMemo(() => {
        const map = new Map<SurfacePlatformValue, AssistantSurface[]>();
        for (const surface of surfaces) {
            const platform = getSurfacePlatform(surface);
            const list = map.get(platform);
            if (list) list.push(surface);
            else map.set(platform, [surface]);
        }
        return map;
    }, [surfaces]);
    const liveCount = surfaces.reduce((count, surface) => count + (surface.status === 'ACTIVE' ? 1 : 0), 0);
    const needsSetupCount = surfaces.filter((surface) => {
        const status = getSurfaceStatus(surface);
        return status.tone === 'warning' || status.tone === 'danger';
    }).length;

    const editingPlatform = editTarget?.platform ?? null;
    const isCreatingSurface = editTarget?.mode === 'create';
    const editingDefinition = SURFACE_DEFINITIONS.find((definition) => definition.platform === editingPlatform) ?? null;
    const editingAccounts = editingPlatform ? accounts.filter((account) => accountMatchesPlatform(account, editingPlatform)) : [];
    const editingSurface = editTarget?.mode === 'edit'
        ? (surfacesByPlatform.get(editTarget.platform) ?? []).find((surface) => surface.name === editTarget.surfaceName)
        : undefined;
    const selectedAssistantName = draftAgentName !== DEFAULT_AGENT_VALUE && assistants.some((assistant) => assistant.name === draftAgentName)
        ? draftAgentName
        : null;
    const requiresAccount = editingDefinition
        ? platformRequiresAccount(editingDefinition.platform) || (platformSupportsManagedIdentity(editingDefinition.platform) && draftIdentityMode === 'CONNECTED')
        : false;
    const supportsChannelRoutes = editingDefinition ? platformSupportsChannelRoutes(editingDefinition.platform) : false;
    // Channels can only be enumerated once the surface exists (we need its
    // connected account to list them), so routing is an existing-surface step.
    const channelRoutingEnabled = supportsChannelRoutes && Boolean(editingSurface);
    const { data: availableChannelsData, isLoading: isLoadingChannels } = useSurfaceChannels(
        podId,
        editingSurface?.name,
        channelRoutingEnabled
    );
    const availableChannels = (availableChannelsData?.channels ?? []) as AvailableChannel[];
    const usedChannelIds = new Set(draftChannels.map((route) => route.channel_id).filter(Boolean));
    const remainingChannels = availableChannels.filter((channel) => !usedChannelIds.has(channel.id));

    // Name handling: the first surface of a platform can use the default name
    // (lowercased platform); adding another requires a distinct, unused name.
    const existingNames = useMemo(
        () => new Set((editingPlatform ? surfacesByPlatform.get(editingPlatform) ?? [] : []).map((surface) => surface.name.toLowerCase())),
        [editingPlatform, surfacesByPlatform]
    );
    const defaultSurfaceName = editingDefinition ? editingDefinition.platform.toLowerCase() : '';
    const effectiveName = (draftName.trim() || defaultSurfaceName).toLowerCase();
    const nameCollision = isCreatingSurface && existingNames.has(effectiveName);
    const nameValid = !isCreatingSurface || (effectiveName.length > 0 && !nameCollision);

    const canSave =
        Boolean(editingDefinition) &&
        nameValid &&
        (!requiresAccount || Boolean(draftAccountId)) &&
        draftChannels.every((route) => Boolean(route.channel_id));
    const isSaving = isCreating || isUpdating;

    const addRoute = () => {
        const next = remainingChannels[0];
        setDraftChannels((prev) => [
            ...prev,
            { channel_id: next?.id ?? '', channel_name: next?.name ?? '', agent_name: null },
        ]);
    };
    const updateRoute = (index: number, patch: Partial<ChannelDraft>) =>
        setDraftChannels((prev) => prev.map((route, i) => (i === index ? { ...route, ...patch } : route)));
    const removeRoute = (index: number) =>
        setDraftChannels((prev) => prev.filter((_, i) => i !== index));

    const loadDraftsFromSurface = (definition: SurfaceDefinition, existing?: AssistantSurface) => {
        const config = existing?.config || {};
        const channels = config.channels || [];
        const identity = config.identity || {};
        const platformAccounts = accounts.filter((account) => accountMatchesPlatform(account, definition.platform));
        const accountId = existing?.account_id && platformAccounts.some((account) => account.id === existing.account_id)
            ? existing.account_id
            : (platformAccounts.find((account) => account.is_default)?.id ?? platformAccounts[0]?.id ?? '');

        setDraftName(existing?.name || '');
        setDraftAgentName(existing?.agent_name || DEFAULT_AGENT_VALUE);
        setDraftAccountId(accountId);
        setDraftIdentityMode(platformSupportsManagedIdentity(definition.platform) && existing?.account_id ? 'CONNECTED' : 'BUILT_IN');
        setDraftChannels(
            channels.map((route) => ({
                channel_id: route.channel_id || '',
                channel_name: route.channel_name || '',
                agent_name: route.agent_name ?? null,
            }))
        );
        setDraftAllowedDomains((identity.allowed_domains || []).join(', '));
        setDraftAllowedEmails((identity.allowed_email_addresses || []).join(', '));
        setDraftAllowSend(Boolean((config as { send_policy?: { allow_send?: boolean } }).send_policy?.allow_send));
    };

    const openCreate = (definition: SurfaceDefinition) => {
        loadDraftsFromSurface(definition, undefined);
        setEditTarget({ mode: 'create', platform: definition.platform });
    };

    const openEdit = (definition: SurfaceDefinition, surface: AssistantSurface) => {
        loadDraftsFromSurface(definition, surface);
        setEditTarget({ mode: 'edit', platform: definition.platform, surfaceName: surface.name });
    };

    const closeConfig = () => setEditTarget(null);

    const handleToggle = (surfaceName: string, isCurrentlyActive: boolean) => {
        toggleSurface(
            { podId, surfaceName, isActive: !isCurrentlyActive },
            {
                onSuccess: () => toast.success(isCurrentlyActive ? 'Surface paused' : 'Surface turned on'),
                onError: (error) => toast.error(`Failed to update surface: ${error.message}`),
            }
        );
    };

    const handleDeleteSurface = () => {
        if (!editingDefinition || !editingSurface) return;
        deleteSurface(
            { podId, surfaceName: editingSurface.name },
            {
                onSuccess: () => {
                    toast.success(`${editingDefinition.label} surface removed`);
                    closeConfig();
                },
                onError: (error) => toast.error(`Failed to remove ${editingDefinition.label}: ${error.message}`),
            }
        );
    };

    const handleSaveSurface = () => {
        if (!editingDefinition) return;
        if (requiresAccount && !draftAccountId) {
            toast.error(`Connect ${editingDefinition.label} first`);
            return;
        }
        // Channel routes are optional; a Slack/Teams surface answers DMs without
        // any. Only persist rows that actually picked a channel.
        const channels = draftChannels
            .filter((route) => route.channel_id)
            .map((route) => ({
                channel_id: route.channel_id,
                channel_name: route.channel_name || null,
                agent_name: route.agent_name,
            }));
        const isEmail = platformIsEmailSurface(editingDefinition.platform);

        const config: SurfaceBehaviorConfigInput = {
            dm_conversation_reset_after_hours: DEFAULT_DM_RESET_HOURS,
            ...(supportsChannelRoutes ? { channels } : {}),
            ...(isEmail
                ? {
                      identity: {
                          allowed_domains: parseList(draftAllowedDomains),
                          allowed_email_addresses: parseList(draftAllowedEmails),
                      },
                  }
                : {}),
            send_policy: { allow_send: draftAllowSend },
        };

        // Shared by create and update; the surface's platform and name are set
        // once at creation and are immutable thereafter.
        const shared = {
            default_agent_name: selectedAssistantName,
            is_enabled: true,
            credential_mode: (requiresAccount ? 'CUSTOM' : 'SYSTEM') as SurfaceCredentialMode,
            ...(requiresAccount ? { account_id: draftAccountId } : {}),
            config,
        };

        const callbacks = {
            onSuccess: () => {
                toast.success(`${editingDefinition.label} surface is on`);
                closeConfig();
            },
            onError: (error: Error) => toast.error(`Failed to save ${editingDefinition.label}: ${error.message}`),
        };

        if (isCreatingSurface) {
            createSurface(
                {
                    podId,
                    data: {
                        platform: editingDefinition.platform as SurfacePlatform,
                        name: draftName.trim() || undefined,
                        ...shared,
                    },
                },
                callbacks
            );
        } else if (editingSurface) {
            updateSurface({ podId, surfaceName: editingSurface.name, data: shared }, callbacks);
        }
    };

    return (
        <div id="surfaces" className="surfaces-page-shell">
            <section className="surfaces-ledger-section">
                {showHeading ? (
                    <div className="surfaces-section-heading">
                        <div>
                            <h1>Surfaces</h1>
                            <p>Turn on the places where this pod should listen, route, and answer.</p>
                        </div>
                        <Button variant="ghost" size="icon" onClick={() => void refetch()} disabled={isFetching} aria-label="Refresh surfaces" className="h-8 w-8 rounded">
                            {isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                        </Button>
                    </div>
                ) : null}

                <div className="surfaces-hero-meta">
                    <span>{liveCount} live</span>
                    {needsSetupCount > 0 ? <span>{needsSetupCount} need attention</span> : <span>{SURFACE_DEFINITIONS.length} available</span>}
                </div>

                {isLoading ? (
                    <div className="surfaces-loading-row">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading surfaces
                    </div>
                ) : (
                    <div className="surfaces-platform-list">
                        {SURFACE_DEFINITIONS.map((definition) => (
                            <SurfacePlatformGroup
                                key={definition.platform}
                                definition={definition}
                                surfaces={surfacesByPlatform.get(definition.platform) ?? []}
                                accounts={accounts}
                                isBusy={isToggling || isSaving}
                                onAdd={() => openCreate(definition)}
                                onConfigure={(surface) => openEdit(definition, surface)}
                                onToggle={(surface) => handleToggle(surface.name, surface.status === 'ACTIVE')}
                                onSend={(surface) => setSendTarget({ surfaceName: surface.name, label: surfaceSendLabel(definition, surface) })}
                            />
                        ))}
                    </div>
                )}
            </section>

            <Dialog open={editTarget !== null} onOpenChange={(open) => {
                if (!open) closeConfig();
            }}>
                <DialogContent className="max-w-2xl">
                    {editingDefinition ? (
                        <>
                            <DialogHeader>
                                <DialogTitle>
                                    {isCreatingSurface
                                        ? `Add ${editingDefinition.label} surface`
                                        : `Configure ${editingSurface?.name ?? editingDefinition.label}`}
                                </DialogTitle>
                                <DialogDescription>
                                    {getConfigurationDescription(editingDefinition.platform)}
                                </DialogDescription>
                            </DialogHeader>

                            <div className="grid gap-4 py-1">
                                {isCreatingSurface ? (
                                    <div className="grid gap-2">
                                        <label className="type-eyebrow-medium">Surface name</label>
                                        <Input
                                            value={draftName}
                                            onChange={(event) => setDraftName(event.target.value)}
                                            placeholder={defaultSurfaceName}
                                            aria-invalid={nameCollision}
                                        />
                                        <p className={cn('text-xs leading-5', nameCollision ? 'text-[var(--state-error)]' : 'text-[var(--text-tertiary)]')}>
                                            {nameCollision
                                                ? 'A surface with this name already exists on this pod.'
                                                : `Optional. Defaults to “${defaultSurfaceName}”. Add a name to run a second ${editingDefinition.label} surface routed to its own agent.`}
                                        </p>
                                    </div>
                                ) : (
                                    <div className="grid gap-1">
                                        <label className="type-eyebrow-medium">Surface name</label>
                                        <p className="text-sm text-[var(--text-primary)]">{editingSurface?.name}</p>
                                        <p className="text-xs leading-5 text-[var(--text-tertiary)]">Fixed once the surface is created.</p>
                                    </div>
                                )}
                                <div className="grid gap-2">
                                    <label className="type-eyebrow-medium">
                                        Default responder
                                    </label>
                                    <Select value={draftAgentName} onValueChange={setDraftAgentName}>
                                        <SelectTrigger className="h-10 bg-[var(--field-bg)]">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value={DEFAULT_AGENT_VALUE}>
                                                Pod default agent
                                            </SelectItem>
                                            {assistants.map((assistant) => (
                                                <SelectItem key={assistant.id || assistant.name} value={assistant.name}>
                                                    {assistant.name}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <p className="text-xs text-[var(--text-tertiary)]">
                                        {supportsChannelRoutes
                                            ? 'Answers DMs and any channel without its own route below.'
                                            : 'Leave this as pod default unless this surface should talk to a specific agent.'}
                                    </p>
                                </div>

                                {editingDefinition.builtInIdentity ? (
                                    <div className="grid gap-2">
                                        <label className="type-eyebrow-medium">
                                            {getIdentityFieldLabel(editingDefinition)} identity
                                        </label>
                                        <div className="grid gap-2 sm:grid-cols-2">
                                            {(['BUILT_IN', 'CONNECTED'] as IdentityMode[]).map((identityMode) => {
                                                const selected = draftIdentityMode === identityMode;
                                                return (
                                                    <button
                                                        key={identityMode}
                                                        type="button"
                                                        onClick={() => setDraftIdentityMode(identityMode)}
                                                        className={cn(
                                                            'surface-picker-button surface-choice-row custom-focus-ring',
                                                            selected && 'is-selected'
                                                        )}
                                                    >
                                                        <span className="surface-choice-icon">
                                                            {identityMode === 'BUILT_IN' ? <Bot className="h-4 w-4" /> : <Plug className="h-4 w-4" />}
                                                        </span>
                                                        <span className="min-w-0 flex-1">
                                                            <span className="surface-choice-title">{getIdentityOptionLabel(editingDefinition, identityMode)}</span>
                                                            <span className="surface-choice-copy">{getIdentityOptionHelpText(editingDefinition, identityMode)}</span>
                                                        </span>
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    </div>
                                ) : null}

                                {requiresAccount ? (
                                    <div className="grid gap-2">
                                        <label className="type-eyebrow-medium">{editingDefinition.accountLabel}</label>
                                        {editingAccounts.length > 0 ? (
                                            <Select value={draftAccountId} onValueChange={setDraftAccountId}>
                                                <SelectTrigger className="h-10 bg-[var(--field-bg)]">
                                                    <SelectValue placeholder={`Select ${editingDefinition.accountLabel}`} />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {editingAccounts.map((account) => (
                                                        <SelectItem key={account.id} value={account.id}>
                                                            {formatAccountLabel(account)}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        ) : (
                                            <div className="surface-inline-callout">
                                                <p className="text-sm font-normal text-[var(--text-primary)]">Connect {editingDefinition.label} first</p>
                                                <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">This surface needs a connected account before it can receive events.</p>
                                                <Button asChild className="mt-3" size="sm" variant="outline">
                                                    <Link href={`/pod/${podId}/connectors`}>
                                                        Open connectors
                                                    </Link>
                                                </Button>
                                            </div>
                                        )}
                                    </div>
                                ) : null}

                                {platformIsEmailSurface(editingDefinition.platform) ? (
                                    <div className="grid gap-3 rounded-lg border border-[color:var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] p-3">
                                        <div>
                                            <p className="text-sm font-medium text-[var(--text-primary)]">Email filters</p>
                                            <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                                                Only emails from these senders become pod work. A busy mailbox gets a lot of noise — set at least one filter so the agent ignores everything else.
                                            </p>
                                        </div>
                                        <div className="grid gap-1.5">
                                            <label className="type-eyebrow-medium">Allowed domains</label>
                                            <Input
                                                value={draftAllowedDomains}
                                                onChange={(event) => setDraftAllowedDomains(event.target.value)}
                                                placeholder="acme.com, partner.org"
                                            />
                                            <p className="text-xs leading-5 text-[var(--text-tertiary)]">Comma-separated. Any sender at these domains is handled.</p>
                                        </div>
                                        <div className="grid gap-1.5">
                                            <label className="type-eyebrow-medium">Allowed email addresses</label>
                                            <Input
                                                value={draftAllowedEmails}
                                                onChange={(event) => setDraftAllowedEmails(event.target.value)}
                                                placeholder="vip@acme.com, support@partner.org"
                                            />
                                            <p className="text-xs leading-5 text-[var(--text-tertiary)]">Comma-separated. Specific addresses to always handle.</p>
                                        </div>
                                        {!parseList(draftAllowedDomains).length && !parseList(draftAllowedEmails).length ? (
                                            <p className="text-xs leading-5 text-[var(--state-warning)]">
                                                No filters set — every email to this mailbox will be handled.
                                            </p>
                                        ) : null}
                                    </div>
                                ) : null}

                                {supportsChannelRoutes ? (
                                    <div className="grid gap-3 rounded-lg border border-[color:var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] p-3">
                                        <div>
                                            <p className="text-sm font-medium text-[var(--text-primary)]">Channel routing</p>
                                            <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                                                Send specific channels to a chosen agent. In a channel the agent only replies when mentioned or in a thread it’s already in.
                                            </p>
                                        </div>

                                        {!editingSurface ? (
                                            <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                                                Turn on {editingDefinition.label} first (it handles DMs right away), then reopen Configure to route channels.
                                            </p>
                                        ) : isLoadingChannels ? (
                                            <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                                                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading channels…
                                            </div>
                                        ) : availableChannels.length === 0 && draftChannels.length === 0 ? (
                                            <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                                                No channels found. Invite the {editingDefinition.label} bot to a channel, then refresh.
                                            </p>
                                        ) : (
                                            <>
                                                {draftChannels.map((route, index) => {
                                                    const otherUsed = new Set(
                                                        draftChannels
                                                            .filter((_, i) => i !== index)
                                                            .map((other) => other.channel_id)
                                                            .filter(Boolean)
                                                    );
                                                    const options = availableChannels.filter((channel) => !otherUsed.has(channel.id));
                                                    return (
                                                        <ChannelRouteRow
                                                            key={index}
                                                            route={route}
                                                            options={options}
                                                            assistants={assistants}
                                                            onChange={(patch) => updateRoute(index, patch)}
                                                            onRemove={() => removeRoute(index)}
                                                        />
                                                    );
                                                })}
                                                <Button
                                                    type="button"
                                                    variant="outline"
                                                    size="sm"
                                                    className="w-fit"
                                                    onClick={addRoute}
                                                    disabled={remainingChannels.length === 0}
                                                >
                                                    <Plus className="mr-1.5 h-3.5 w-3.5" />
                                                    Add channel
                                                </Button>
                                            </>
                                        )}
                                    </div>
                                ) : null}

                                <div className="flex items-center justify-between gap-3 rounded-lg border border-[color:var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] p-3">
                                    <div className="min-w-0">
                                        <p className="text-sm font-medium text-[var(--text-primary)]">Proactive messages</p>
                                        <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                                            Let agents message pod members first on this surface. Delivery only reuses an existing thread — there is no cold outreach.
                                        </p>
                                    </div>
                                    <Switch
                                        checked={draftAllowSend}
                                        onCheckedChange={setDraftAllowSend}
                                        aria-label="Allow proactive messages"
                                        className="surface-platform-switch"
                                    >
                                        <SwitchTrack className={draftAllowSend ? 'bg-[var(--action-primary)]' : undefined}>
                                            <SwitchThumb className={draftAllowSend ? 'translate-x-4' : undefined} />
                                        </SwitchTrack>
                                    </Switch>
                                </div>

                                {editingSurface ? (
                                    <SurfaceSetupSection podId={podId} surfaceName={editingSurface.name} />
                                ) : null}

                            </div>

                            <DialogFooter className="sm:justify-between">
                                {editingSurface ? (
                                    <Button
                                        variant="ghost"
                                        onClick={handleDeleteSurface}
                                        disabled={isSaving || isDeleting}
                                        className="text-[var(--state-error)]"
                                    >
                                        {isDeleting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                                        Remove surface
                                    </Button>
                                ) : <span />}
                                <div className="flex items-center gap-2">
                                    <Button variant="outline" onClick={closeConfig} disabled={isSaving || isDeleting}>Cancel</Button>
                                    <Button onClick={handleSaveSurface} disabled={!canSave || isSaving || isDeleting}>
                                        {isSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                        Save and turn on
                                    </Button>
                                </div>
                            </DialogFooter>
                        </>
                    ) : null}
                </DialogContent>
            </Dialog>

            <SurfaceSendDialog podId={podId} target={sendTarget} onClose={() => setSendTarget(null)} />
        </div>
    );
}

export const PodChannelsPanel = PodSurfacesPanel;

function ChannelRouteRow({
    route,
    options,
    assistants,
    onChange,
    onRemove,
}: {
    route: ChannelDraft;
    options: AvailableChannel[];
    assistants: Array<{ id?: string | null; name: string }>;
    onChange: (patch: Partial<ChannelDraft>) => void;
    onRemove: () => void;
}) {
    // A pre-existing route whose channel the API no longer lists (bot left it)
    // still needs to render its current selection.
    const missingSelected = Boolean(route.channel_id) && !options.some((channel) => channel.id === route.channel_id);

    return (
        <div className="grid items-end gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <div className="grid gap-1">
                <label className="type-eyebrow-medium">Channel</label>
                <Select
                    value={route.channel_id}
                    onValueChange={(id) => {
                        const picked = options.find((channel) => channel.id === id);
                        onChange({ channel_id: id, channel_name: picked?.name ?? '' });
                    }}
                >
                    <SelectTrigger className="h-9 bg-[var(--field-bg)]">
                        <SelectValue placeholder="Select channel" />
                    </SelectTrigger>
                    <SelectContent>
                        {missingSelected ? (
                            <SelectItem value={route.channel_id}>
                                {route.channel_name ? `#${route.channel_name}` : route.channel_id}
                            </SelectItem>
                        ) : null}
                        {options.map((channel) => (
                            <SelectItem key={channel.id} value={channel.id}>
                                {channel.name ? `#${channel.name}` : channel.id}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <div className="grid gap-1">
                <label className="type-eyebrow-medium">Agent</label>
                <Select
                    value={route.agent_name ?? DEFAULT_AGENT_VALUE}
                    onValueChange={(value) => onChange({ agent_name: value === DEFAULT_AGENT_VALUE ? null : value })}
                >
                    <SelectTrigger className="h-9 bg-[var(--field-bg)]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value={DEFAULT_AGENT_VALUE}>Pod default agent</SelectItem>
                        {assistants.map((assistant) => (
                            <SelectItem key={assistant.id || assistant.name} value={assistant.name}>
                                {assistant.name}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={onRemove}
                aria-label="Remove channel route"
                className="h-9 w-9"
            >
                <Trash2 className="h-4 w-4" />
            </Button>
        </div>
    );
}

function SurfacePlatformMark({ definition }: { definition: SurfaceDefinition }) {
    const Icon = definition.icon;

    return (
        <span className="surface-platform-mark surface-platform-mark-logo" data-platform={definition.platform.toLowerCase()}>
            {definition.logoSrc ? (
                <>
                    <Image src={definition.logoSrc} alt="" width={16} height={16} className="surface-platform-logo" aria-hidden="true" />
                    <Icon className="surface-platform-icon-fallback h-4 w-4" />
                </>
            ) : (
                <Icon className="h-4 w-4" />
            )}
        </span>
    );
}

function surfaceIsPrimary(definition: SurfaceDefinition, surface: AssistantSurface) {
    return surface.name.toLowerCase() === definition.platform.toLowerCase();
}

function surfaceDisplayName(definition: SurfaceDefinition, surface: AssistantSurface) {
    return surfaceIsPrimary(definition, surface) ? definition.label : surface.name;
}

function surfaceSendLabel(definition: SurfaceDefinition, surface: AssistantSurface) {
    const display = surfaceDisplayName(definition, surface);
    return display === definition.label ? definition.label : `${definition.label} · ${display}`;
}

function SurfacePlatformGroup({
    definition,
    surfaces,
    accounts,
    isBusy,
    onAdd,
    onConfigure,
    onToggle,
    onSend,
}: {
    definition: SurfaceDefinition;
    surfaces: AssistantSurface[];
    accounts: Account[];
    isBusy: boolean;
    onAdd: () => void;
    onConfigure: (surface: AssistantSurface) => void;
    onToggle: (surface: AssistantSurface) => void;
    onSend: (surface: AssistantSurface) => void;
}) {
    const isEmpty = surfaces.length === 0;

    return (
        <article className="surface-platform-group">
            <div className="flex min-w-0 items-center gap-3">
                <SurfacePlatformMark definition={definition} />
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-medium leading-5 text-[var(--text-primary)]">{definition.label}</h3>
                        {isEmpty ? (
                            <StatusPill label="Off" tone="muted" />
                        ) : surfaces.length > 1 ? (
                            <span className="chip chip-sm chip-muted shrink-0">{surfaces.length}</span>
                        ) : null}
                    </div>
                    <p className="mt-0.5 text-xs leading-5 text-[var(--text-tertiary)]">{getSurfaceNuance(definition.platform)}</p>
                </div>
            </div>

            {isEmpty ? (
                <p className="text-xs leading-5 text-[var(--text-secondary)]">{getSurfaceEmptyDetail(definition.platform)}</p>
            ) : (
                <div className="surface-instance-list">
                    {surfaces.map((surface) => (
                        <SurfaceInstanceRow
                            key={surface.id ?? surface.name}
                            definition={definition}
                            surface={surface}
                            account={surface.account_id ? accounts.find((account) => account.id === surface.account_id) : undefined}
                            isBusy={isBusy}
                            onConfigure={() => onConfigure(surface)}
                            onToggle={() => onToggle(surface)}
                            onSend={() => onSend(surface)}
                        />
                    ))}
                </div>
            )}

            <Button type="button" variant="link" size="xs" onClick={onAdd} disabled={isBusy} className="w-fit gap-1.5">
                <Plus className="h-3.5 w-3.5" />
                {isEmpty ? `Add ${definition.label} surface` : `Add another ${definition.label} surface`}
            </Button>
        </article>
    );
}

function SurfaceInstanceRow({
    definition,
    surface,
    account,
    isBusy,
    onConfigure,
    onToggle,
    onSend,
}: {
    definition: SurfaceDefinition;
    surface: AssistantSurface;
    account?: Account;
    isBusy: boolean;
    onConfigure: () => void;
    onToggle: () => void;
    onSend: () => void;
}) {
    const status = getSurfaceStatus(surface);
    const isOn = surface.status === 'ACTIVE';
    const detail = getSurfaceConfiguredDetail(surface, account, definition);
    const isPrimary = surfaceIsPrimary(definition, surface);

    return (
        <div className="surface-instance-row">
            <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium leading-5 text-[var(--text-primary)]">
                        {isPrimary ? 'Default' : surface.name}
                    </span>
                    <StatusPill label={status.label} tone={status.tone} />
                </div>
                <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{detail}</p>
            </div>

            <div className="surface-platform-actions">
                {isOn ? (
                    <Button
                        size="xs"
                        variant="ghost"
                        onClick={onSend}
                        disabled={isBusy}
                        aria-label="Message a member"
                        title="Message a member"
                    >
                        <Send className="h-3.5 w-3.5" />
                    </Button>
                ) : null}
                <Button size="xs" variant="outline" onClick={onConfigure} disabled={isBusy}>
                    Configure
                </Button>
                <Switch
                    checked={isOn}
                    onCheckedChange={() => onToggle()}
                    disabled={isBusy}
                    aria-label={`${isOn ? 'Turn off' : 'Turn on'} ${surfaceDisplayName(definition, surface)}`}
                    className="surface-platform-switch"
                >
                    <SwitchTrack className={isOn ? 'bg-[var(--action-primary)]' : undefined}>
                        <SwitchThumb className={isOn ? 'translate-x-4' : undefined} />
                    </SwitchTrack>
                </Switch>
            </div>
        </div>
    );
}

function StatusPill({ label, tone }: { label: string; tone: SurfaceTone }) {
    const toneClass =
        tone === 'success'
            ? 'state-badge-success'
            : tone === 'warning'
                ? 'state-badge-warning'
                : tone === 'danger'
                    ? 'state-badge-error'
                    : tone === 'info'
                        ? 'state-badge-info'
                        : 'chip-muted';

    return (
        <span className={cn('chip chip-sm shrink-0', toneClass)}>
            {label}
        </span>
    );
}

function SurfaceSetupSection({ podId, surfaceName }: { podId: string; surfaceName: string }) {
    const { data: setup, isLoading } = useSurfaceSetup(podId, surfaceName);

    if (isLoading) {
        return (
            <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking setup…
            </div>
        );
    }
    if (!setup || !setup.exists) return null;

    const actions = setup.actions ?? [];
    const consent = setup.admin_consent;
    const needsConsent = Boolean(consent?.required && !consent?.granted && consent?.consent_url);

    // Nothing for the user to do (system credentials, auto-registered webhooks,
    // or consent already granted): show a clean "Ready" state instead of noise.
    if (!actions.length && !needsConsent) {
        return (
            <div className="flex items-center gap-2 rounded-lg border border-[color:var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                <CheckCircle2 className="h-4 w-4 shrink-0 text-[var(--state-success)]" />
                Ready — nothing to configure.
            </div>
        );
    }

    return (
        <div className="grid gap-3">
            <p className="type-eyebrow-medium">Finish setup</p>
            {needsConsent ? (
                <a
                    href={consent!.consent_url as string}
                    target="_blank"
                    rel="noreferrer"
                    className="surface-inline-callout flex items-center justify-between gap-2 text-sm text-[var(--text-primary)] hover:underline"
                >
                    <span className="flex items-center gap-2"><ShieldCheck className="h-4 w-4" /> Grant admin consent</span>
                    <ExternalLink className="h-3.5 w-3.5" />
                </a>
            ) : null}
            {actions.map((action) => (
                <SetupActionCard key={action.key} action={action} />
            ))}
        </div>
    );
}

function SetupActionCard({ action }: { action: SurfaceSetupAction }) {
    const fields = action.fields ?? [];
    const steps = action.steps ?? [];

    return (
        <div className="grid gap-3 rounded-lg border border-[color:var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] p-3">
            <div>
                <p className="text-sm font-medium text-[var(--text-primary)]">{action.title}</p>
                {action.description ? (
                    <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{action.description}</p>
                ) : null}
            </div>

            {fields.length ? (
                <div className="grid gap-2">
                    {fields.map((field, index) => (
                        <SetupCopyField key={`${field.label}-${index}`} field={field} />
                    ))}
                </div>
            ) : null}

            {steps.length ? (
                <ol className="grid gap-1.5">
                    {steps.map((step, index) => (
                        <li key={index} className="flex gap-2 text-xs leading-5 text-[var(--text-secondary)]">
                            <span className="chip chip-sm flex h-4 w-4 shrink-0 items-center justify-center rounded-full p-0 text-xs font-medium">
                                {index + 1}
                            </span>
                            <span className="min-w-0">{step}</span>
                        </li>
                    ))}
                </ol>
            ) : null}

            {action.link ? (
                <a
                    href={action.link}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex w-fit items-center gap-1.5 text-xs font-medium text-[var(--action-primary)] hover:underline"
                >
                    {action.link_label || 'Open dashboard'} <ExternalLink className="h-3.5 w-3.5" />
                </a>
            ) : null}
        </div>
    );
}

function SetupCopyField({ field }: { field: SurfaceSetupActionField }) {
    const [copied, setCopied] = useState(false);

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(field.value);
            setCopied(true);
            playSoundFeedback('action-success');
            setTimeout(() => setCopied(false), 1500);
        } catch {
            toast.error('Could not copy to clipboard');
        }
    };

    return (
        <div className="grid gap-1">
            <span className="type-eyebrow-medium">{field.label}</span>
            <button
                type="button"
                onClick={copy}
                className="custom-focus-ring flex items-center justify-between gap-2 rounded-md border border-[color:var(--border-subtle)] bg-[var(--field-bg)] px-2.5 py-1.5 text-left"
                aria-label={`Copy ${field.label}`}
            >
                <span className="min-w-0 break-all font-mono text-xs text-[var(--text-primary)]">{field.value}</span>
                {copied ? (
                    <Check className="h-3.5 w-3.5 shrink-0 text-[var(--state-success)]" />
                ) : (
                    <Copy className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                )}
            </button>
        </div>
    );
}

function parseList(raw: string): string[] {
    const out: string[] = [];
    for (const token of raw.split(/[\s,;]+/)) {
        const value = token.trim().toLowerCase();
        if (value && !out.includes(value)) out.push(value);
    }
    return out;
}

function platformRequiresAccount(platform: SurfacePlatformValue) {
    return platform === 'SLACK' || platform === 'TEAMS' || platform === 'GMAIL' || platform === 'OUTLOOK';
}

function platformSupportsManagedIdentity(platform: SurfacePlatformValue) {
    return platform === 'TELEGRAM' || platform === 'WHATSAPP';
}

function platformSupportsChannelRoutes(platform: SurfacePlatformValue) {
    return platform === 'SLACK' || platform === 'TEAMS';
}

function platformIsEmailSurface(platform: SurfacePlatformValue) {
    return platform === 'GMAIL' || platform === 'OUTLOOK' || platform === 'RESEND';
}

function getConfigurationDescription(platform: SurfacePlatformValue) {
    if (platformSupportsChannelRoutes(platform)) {
        return 'Choose the connected workspace and map a channel to the agent that should answer there.';
    }
    if (platform === 'RESEND') {
        return 'Set the default agent and sender filters for email handled through Lemma’s managed Resend address.';
    }
    if (platformIsEmailSurface(platform)) {
        return 'Choose the mailbox and default agent for emails that should become pod work.';
    }
    if (platform === 'TELEGRAM') return 'Choose Lemma bot or your own Telegram bot.';
    if (platform === 'WHATSAPP') return 'Choose Lemma number or your own WhatsApp account.';
    return 'Choose how this surface should reach the pod.';
}

function getSurfaceNuance(platform: SurfacePlatformValue) {
    if (platform === 'SLACK') return 'Routing: channel and mention behavior.';
    if (platform === 'TEAMS') return 'Routing: team channel and mention behavior.';
    if (platform === 'GMAIL') return 'Routes all eligible inbox events into the pod.';
    if (platform === 'OUTLOOK') return 'Routes all eligible mailbox messages into the pod.';
    if (platform === 'RESEND') return 'Managed email via Resend — no mailbox to connect.';
    if (platform === 'TELEGRAM') return 'Identity: Lemma bot by default.';
    if (platform === 'WHATSAPP') return 'Identity: WhatsApp webhook setup and default responder.';
    return 'Configure this platform before turning it on.';
}

function getSurfaceEmptyDetail(platform: SurfacePlatformValue) {
    if (platformRequiresAccount(platform)) return 'Not connected yet. Add a surface to choose an account and configure rules.';
    return 'Not connected yet. Add a surface to configure the default responder.';
}

function getSurfaceConfiguredDetail(surface: AssistantSurface, account: Account | undefined, definition: SurfaceDefinition) {
    const channels = surface.config?.channels || [];
    if (platformSupportsChannelRoutes(definition.platform)) {
        const route = channels[0];
        const channel = route ? route.channel_name || route.channel_id || '' : '';
        return [
            account ? formatAccountLabel(account) : 'Connected workspace',
            channel ? `routes ${channel}` : 'no channel route yet',
        ].join(' / ');
    }
    if (platformIsEmailSurface(definition.platform)) {
        return [
            account ? formatAccountLabel(account) : 'Connected mailbox',
            'all eligible inbox events',
        ].join(' / ');
    }
    return account ? formatAccountLabel(account) : surface.surface_identity_username || definition.targetLabel;
}

function getSurfaceStatus(surface: AssistantSurface): { label: string; tone: SurfaceTone } {
    const rawStatus = String(surface.status || '').toUpperCase();

    if (rawStatus === 'PENDING_ADMIN_CONSENT') return { label: 'Needs consent', tone: 'warning' };
    if (rawStatus === 'ERROR') return { label: 'Error', tone: 'danger' };
    if (rawStatus === 'ACTIVE') return { label: 'Live', tone: 'success' };
    if (rawStatus === 'INACTIVE' || rawStatus === 'NEEDS_SETUP') return { label: 'Paused', tone: 'muted' };
    return { label: formatDisplayName(rawStatus || 'Unknown'), tone: 'muted' };
}

function getSurfacePlatform(surface: AssistantSurface): SurfacePlatformValue {
    const config = surface.config as Record<string, unknown>;
    const raw = typeof surface.platform === 'string' && surface.platform
        ? surface.platform
        : typeof config.type === 'string'
            ? config.type
            : 'SLACK';

    return raw.toUpperCase() as SurfacePlatformValue;
}

function accountMatchesPlatform(account: Account, platform: SurfacePlatformValue) {
    // Match on the app identity only — NOT account.email (a @gmail.com address
    // belongs to Calendar too) — and use exact provider slugs, not broad
    // 'google'/'microsoft' (which pulled Calendar into Gmail, OneDrive into
    // Outlook, etc.).
    const haystack = [
        account.connector?.title,
        account.connector?.name,
        account.connector_id,
    ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

    const needles: Record<SurfacePlatformValue, string[]> = {
        SLACK: ['slack'],
        TEAMS: ['teams'],
        GMAIL: ['gmail'],
        OUTLOOK: ['outlook'],
        WHATSAPP: ['whatsapp'],
        TELEGRAM: ['telegram'],
        RESEND: ['resend'],
    };

    return needles[platform].some((needle) => haystack.includes(needle));
}


function getIdentityFieldLabel(definition: SurfaceDefinition) {
    if (definition.platform === 'TELEGRAM') return 'Bot';
    if (definition.platform === 'WHATSAPP') return 'Number';
    return 'Identity';
}

function getIdentityOptionLabel(definition: SurfaceDefinition, mode: IdentityMode) {
    if (definition.platform === 'TELEGRAM') return mode === 'BUILT_IN' ? 'Lemma bot' : 'Your bot';
    if (definition.platform === 'WHATSAPP') return mode === 'BUILT_IN' ? 'Lemma number' : 'Your number';
    return mode === 'BUILT_IN' ? 'Lemma' : 'Connected';
}

function getIdentityOptionHelpText(definition: SurfaceDefinition, mode: IdentityMode) {
    if (definition.platform === 'TELEGRAM') {
        return mode === 'BUILT_IN'
            ? 'Use Lemma’s shared Telegram bot.'
            : 'Use a Telegram bot account from Connectors.';
    }
    if (definition.platform === 'WHATSAPP') {
        return mode === 'BUILT_IN'
            ? 'Use Lemma’s shared WhatsApp number.'
            : 'Use a WhatsApp account from Connectors.';
    }
    return mode === 'BUILT_IN'
        ? 'Use a Lemma-managed identity.'
        : 'Use an account from Connectors.';
}


function formatAccountLabel(account: Account) {
    return account.display_name || account.email || account.connector?.title || account.connector?.name || account.id;
}


function formatDisplayName(value: string | null | undefined) {
    const cleaned = (value || '')
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    if (!cleaned) return 'Untitled';

    return cleaned
        .split(' ')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

function SurfaceSendDialog({
    podId,
    target,
    onClose,
}: {
    podId: string;
    target: SurfaceSendTarget | null;
    onClose: () => void;
}) {
    // Only fetch members once the dialog is actually opened for a surface.
    const { data: membersData } = usePodMembers(target ? podId : '');
    const members = membersData?.items ?? [];
    const { mutate: send, isPending } = useSendSurfaceMessage();
    const [userId, setUserId] = useState('');
    const [message, setMessage] = useState('');

    const reset = () => {
        setUserId('');
        setMessage('');
    };

    const close = () => {
        onClose();
        reset();
    };

    const submit = () => {
        const trimmed = message.trim();
        if (!target || !userId || !trimmed) return;
        send(
            { podId, surfaceName: target.surfaceName, userId, message: trimmed },
            {
                onSuccess: (result) => {
                    if (result?.sent) {
                        toast.success('Message sent');
                    } else {
                        toast.warning('That member has no reachable thread on this surface yet');
                    }
                    close();
                },
                onError: (error) => toast.error(`Couldn’t send message: ${error.message}`),
            }
        );
    };

    return (
        <Dialog open={target !== null} onOpenChange={(open) => { if (!open) close(); }}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>Message a member</DialogTitle>
                    <DialogDescription>
                        Send a proactive message through {target?.label ?? 'this surface'}. It reaches the member only if
                        they already have a conversation on this surface — there is no cold outreach.
                    </DialogDescription>
                </DialogHeader>

                <div className="grid gap-4 py-1">
                    <div className="grid gap-1.5">
                        <label className="type-eyebrow-medium">Pod member</label>
                        <Select value={userId} onValueChange={setUserId}>
                            <SelectTrigger className="h-10 bg-[var(--field-bg)]">
                                <SelectValue placeholder="Select a member" />
                            </SelectTrigger>
                            <SelectContent>
                                {members.map((member) => (
                                    <SelectItem key={member.user_id} value={member.user_id}>
                                        {member.user_name || member.user_email}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="grid gap-1.5">
                        <label className="type-eyebrow-medium">Message</label>
                        <Textarea
                            value={message}
                            onChange={(event) => setMessage(event.target.value)}
                            placeholder="What should the agent say?"
                            rows={4}
                        />
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={close} disabled={isPending}>Cancel</Button>
                    <Button onClick={submit} disabled={!userId || !message.trim() || isPending}>
                        {isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
                        Send message
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
