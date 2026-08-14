'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
    AlertTriangle,
    ArrowLeft,
    MoreHorizontal,
    Send,
    Trash2,
} from '@/components/ui/icons';
import { toast } from 'sonner';

import { PlatformMark } from '@/components/surfaces/platform-mark';
import { SurfaceConfigureStep, DEFAULT_AGENT_VALUE, type AvailableChannel, type ConfigureDraft } from '@/components/surfaces/surface-configure-step';
import { SurfaceConnectStep, type CredentialValues } from '@/components/surfaces/surface-connect-step';
import { SurfaceIdentityStep } from '@/components/surfaces/surface-identity-step';
import { SurfaceLiveStep } from '@/components/surfaces/surface-live-step';
import { SurfaceMessageStep } from '@/components/surfaces/surface-message-step';
import { SurfaceProvisioningStep } from '@/components/surfaces/surface-provisioning-step';
import { SurfaceSetupChecklist } from '@/components/surfaces/surface-setup-checklist';
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
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useAssistants } from '@/lib/hooks/use-assistants';
import { useAccounts, useAuthConfigs } from '@/lib/hooks/use-connectors';
import { usePod } from '@/lib/hooks/use-pods';
import {
    useAvailableSurfaces,
    useCreatePodSurface,
    useDeletePodSurface,
    usePodSurfaces,
    useSurfaceChannels,
    useSendSurfaceMessage,
    useStartTelegramManagedBotSetup,
    useSurfaceSetup,
    useTelegramManagedBotSetup,
    useUpdatePodSurface,
    type SurfacePlatformValue,
} from '@/lib/hooks/use-pod-surfaces';
import { findAuthConfigForAccount } from '@/components/connectors/connector-utils';
import { findCatalogSurface, hasSystemIdentity } from '@/lib/surfaces/catalog';
import { deriveSurfaceName } from '@/lib/surfaces/naming';
import { parseCredentialConflict, surfaceErrorMessage } from '@/lib/surfaces/errors';
import {
    forAgent,
    getSurfaceDefinition,
    type SurfaceIdentityMode,
} from '@/lib/surfaces/registry';
import { useConnectSurfaceAccount } from '@/lib/surfaces/use-connect-surface-account';
import { getSurfaceStatus } from '@/lib/utils/surfaces';
import type { AssistantSurface, Account } from '@/lib/types';
import type { SurfaceBehaviorConfigInput, SurfaceCredentialMode, SurfacePlatform } from 'lemma-sdk';
import { cn } from '@/lib/utils';
import { StepLoader } from '@/components/brand/loader';

/**
 * Setting up one surface, as a short sequence of states rather than a form.
 *
 * Each state asks one thing and offers one primary verb; nothing that can be
 * derived is asked. The surface name comes from the platform (and the agent,
 * once a platform holds more than one), and the responder is the agent whose
 * page opened this — which is the point of surfaces living inside agents rather
 * than in a tab of their own.
 *
 *   identity  →  connect  →  live          (a surface that doesn't exist yet)
 *   setup     ⇄  configure                 (one that does)
 */

type SurfaceModalStep =
    | 'identity'
    | 'connect'
    | 'provisioning'
    | 'live'
    | 'configure'
    | 'setup'
    | 'message';

const DEFAULT_DM_RESET_HOURS = 24;

export interface SurfaceModalTarget {
    platform: SurfacePlatformValue;
    /** Present when configuring an existing surface. */
    surfaceName?: string;
    /**
     * `add-channel` opens an installed workspace straight on its routing with a
     * blank row waiting, so "add a channel" is one click from the agent page
     * rather than a settings page you have to read first.
     */
    intent?: 'add-channel';
}

export function SurfaceModal({
    podId,
    target,
    agentName,
    onClose,
}: {
    podId: string;
    target: SurfaceModalTarget | null;
    /** The agent this surface should answer as; `null` = the pod default. */
    agentName: string | null;
    onClose: () => void;
}) {
    const queryClient = useQueryClient();
    const definition = getSurfaceDefinition(target?.platform);

    const { data: pod } = usePod(podId);
    const { data: surfaces = [] } = usePodSurfaces(target ? podId : undefined);
    const { data: catalog } = useAvailableSurfaces(podId, Boolean(target));
    const { data: assistantsData } = useAssistants(target ? podId : '');
    const { data: accounts = [] } = useAccounts({
        organizationId: pod?.organization_id,
        limit: 200,
        enabled: Boolean(target && pod?.organization_id),
    });
    const { data: authConfigs } = useAuthConfigs({
        organizationId: pod?.organization_id,
        limit: 200,
        enabled: Boolean(target && pod?.organization_id),
    });

    const createSurface = useCreatePodSurface();
    const updateSurface = useUpdatePodSurface();
    const deleteSurface = useDeletePodSurface();
    const sendMessage = useSendSurfaceMessage();
    const startManagedSetup = useStartTelegramManagedBotSetup();
    const { connect: connectAccount, isConnecting } = useConnectSurfaceAccount(pod?.organization_id);

    const catalogEntry = findCatalogSurface(catalog, target?.platform);
    const assistants = assistantsData?.items ?? [];

    // Freshly created surfaces are held locally until the pod list catches up,
    // so the live state can render `reach` immediately.
    const [createdSurface, setCreatedSurface] = useState<AssistantSurface | null>(null);
    const [step, setStep] = useState<SurfaceModalStep>('identity');
    const [identityMode, setIdentityMode] = useState<SurfaceIdentityMode | null>(null);
    const [credentials, setCredentials] = useState<CredentialValues>({});
    const [accountId, setAccountId] = useState('');
    // True while the connect journey is repointing an *existing* surface at a
    // different account, rather than creating one. Same states, different verb
    // and a different mutation at the end.
    const [rebinding, setRebinding] = useState(false);
    // The manager-bot hand-off. `launchUrl` is held locally rather than read off
    // the poll, so a transient failure can't strand someone mid-flow with no way
    // back into Telegram.
    const [setupId, setSetupId] = useState<string | null>(null);
    const [launchUrl, setLaunchUrl] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [draft, setDraft] = useState<ConfigureDraft>(emptyDraft());
    const [messageUserId, setMessageUserId] = useState('');
    const [messageBody, setMessageBody] = useState('');
    // Set when the modal is opened on `add-channel`, cleared the moment the draft
    // effect acts on it — a ref rather than state so seeding can't run twice as
    // the surface resolves.
    const pendingChannelRow = useRef(false);

    // Polls only while a hand-off is in flight; the hook stops itself on
    // COMPLETE/FAILED. Declared before `existingSurface` because a managed
    // hand-off is how that surface comes into being.
    const { data: managedSetup, isError: managedSetupFailed } = useTelegramManagedBotSetup(
        podId,
        setupId,
    );

    // The surface being configured.
    const managedSurfaceId = managedSetup?.status === 'COMPLETE' ? managedSetup.surface_id : null;
    const existingSurface = useMemo(() => {
        if (target?.surfaceName) {
            return surfaces.find((surface) => surface.name === target.surfaceName) ?? createdSurface;
        }
        // A managed hand-off never returns the surface to us — the manager bot
        // creates it — so resolve it from the pod list by the id it reports.
        if (managedSurfaceId) {
            return surfaces.find((surface) => surface.id === managedSurfaceId) ?? createdSurface;
        }
        return createdSurface;
    }, [createdSurface, managedSurfaceId, surfaces, target?.surfaceName]);

    const { data: setup, isLoading: isLoadingSetup } = useSurfaceSetup(
        podId,
        existingSurface?.name,
        Boolean(target && existingSurface),
    );
    const setupActions = setup?.actions ?? [];
    const consentUrl = setup?.admin_consent?.required && !setup.admin_consent.granted
        ? setup.admin_consent.consent_url ?? null
        : null;
    // Reference material — a URL worth keeping to hand — is not something the
    // surface is waiting on. Counting it made a live, delivering Slack surface
    // warn that messages wouldn't arrive until setup was finished.
    const hasOutstandingSetup =
        setupActions.some((action) => !action.informational) || Boolean(consentUrl);
    const hasSetupReference = setupActions.length > 0;
    const setupKind: SetupKind = hasOutstandingSetup
        ? 'blocking'
        : hasSetupReference
            ? 'reference'
            : 'none';

    const { data: channelsData, isLoading: isLoadingChannels } = useSurfaceChannels(
        podId,
        existingSurface?.name,
        Boolean(target && existingSurface && definition?.capabilities.channelRoutes),
    );
    const availableChannels = (channelsData?.channels ?? []) as AvailableChannel[];

    // Whether this workspace already runs the org's own app. Read from the auth
    // config behind the surface's account, because that is where the app's
    // credentials live — there is no per-surface answer to this question.
    // Offering "use your own Slack app" to someone already using theirs reads
    // as an invitation to do something they have done.
    const usesOwnApp = useMemo(() => {
        if (!existingSurface?.account_id) return false;
        const account = accounts.find((row) => row.id === existingSurface.account_id);
        if (!account) return false;
        return findAuthConfigForAccount(account, authConfigs)?.config_source === 'ORG_CUSTOM';
    }, [accounts, authConfigs, existingSurface?.account_id]);

    const platformAccounts = useMemo(
        () => accounts.filter((account) => accountMatchesConnector(account, catalogEntry?.connector_id)),
        [accounts, catalogEntry?.connector_id],
    );

    // Entering the modal decides the starting state once: an existing surface
    // opens on whatever it still needs, a new one on its first question.
    const targetKey = target
        ? `${target.platform}:${target.surfaceName ?? ''}:${target.intent ?? ''}`
        : null;
    useEffect(() => {
        if (!targetKey || !definition) return;
        setError(null);
        setCredentials({});
        setSetupId(null);
        setLaunchUrl(null);
        setCreatedSurface(null);
        setRebinding(false);

        if (target?.surfaceName) {
            // Consumed by the draft effect below, which is the only place that
            // knows the surface's existing routes to append to.
            pendingChannelRow.current = target.intent === 'add-channel';
            setStep('configure');
            return;
        }
        setIdentityMode(definition.identityOptions ? null : defaultMode(catalogEntry));
        setAccountId('');
        setStep(definition.identityOptions ? 'identity' : 'connect');
        // `catalogEntry` is intentionally excluded: a late catalog arrival must
        // not reset a step the user has already moved past.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [targetKey]);

    // An existing surface with unfinished setup opens on that, not on settings —
    // but only until the user navigates, hence the one-shot guard on step.
    useEffect(() => {
        if (!target?.surfaceName) return;
        if (step === 'configure' && hasOutstandingSetup) setStep('setup');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [hasOutstandingSetup, target?.surfaceName]);

    useEffect(() => {
        if (!existingSurface) return;
        const base = draftFromSurface(existingSurface);
        if (pendingChannelRow.current) {
            pendingChannelRow.current = false;
            // Routed to the agent whose page opened this, because that is the
            // whole reason someone clicked "add channel" from there.
            setDraft({ ...base, channels: [...base.channels, blankChannelRow(agentName)] });
            return;
        }
        setDraft(base);
        // `agentName` is fixed for the life of a modal target; re-reading it here
        // would only rebuild the draft and discard edits in flight.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [existingSurface]);

    // The manager bot creates the surface server-side, so completion arrives by
    // poll rather than by a mutation result. Adopt the surface it names and move
    // to the proof state; anything else leaves the provisioning state to explain
    // itself.
    useEffect(() => {
        if (managedSetup?.status !== 'COMPLETE') return;
        if (managedSetup.bot_launch_url) setLaunchUrl(managedSetup.bot_launch_url);
        void queryClient.invalidateQueries({ queryKey: ['pod-surfaces', podId] });
        toast.success(
            managedSetup.bot_username
                ? `@${managedSetup.bot_username} is connected`
                : 'Your Telegram bot is connected',
        );
        setStep('live');
    }, [managedSetup, podId, queryClient]);

    const patchDraft = useCallback(
        (patch: Partial<ConfigureDraft>) => setDraft((current) => ({ ...current, ...patch })),
        [],
    );

    if (!target || !definition) return null;

    // Only the first surface of a platform can take the bare platform name, so
    // anything after it is named for the agent it answers as. Derived, never
    // asked — see lib/surfaces/naming.
    const newSurfaceName = deriveSurfaceName(definition.platform, agentName, surfaces);

    const isSystemMode = identityMode === 'SYSTEM';
    const usesJourney = Boolean(definition.journey && catalogEntry?.connect?.credential_schema);
    const isBusy =
        createSurface.isPending
        || updateSurface.isPending
        || deleteSurface.isPending
        || sendMessage.isPending
        || startManagedSetup.isPending
        || isConnecting;

    /** Why the primary verb is disabled, or null when it isn't. */
    const blockedBecause = (): string | null => {
        if (step === 'identity') return identityMode ? null : 'Pick one to continue.';
        if (step === 'connect') {
            if (isSystemMode) return null;
            if (usesJourney) {
                const values = Object.values(credentials).filter((value) => String(value ?? '').trim());
                return values.length ? null : 'Fill in the credentials above.';
            }
            return accountId ? null : `Choose a ${definition.accountLabel.toLowerCase()} first.`;
        }
        if (step === 'message') {
            if (!messageUserId) return 'Pick who this goes to.';
            if (!messageBody.trim()) return 'Write something to send.';
        }
        return null;
    };
    const blocker = blockedBecause();

    /** Ask the manager bot to provision a bot the user will own. */
    const handleStartManagedSetup = async () => {
        setError(null);
        try {
            const setup = await startManagedSetup.mutateAsync({
                podId,
                data: {
                    name: newSurfaceName,
                    default_agent_name: agentName,
                    is_enabled: true,
                    config: { dm_conversation_reset_after_hours: DEFAULT_DM_RESET_HOURS },
                },
            });
            setSetupId(setup.setup_id);
            setLaunchUrl(setup.launch_url);
            setStep('provisioning');
            // Opening it for them saves a hop; the provisioning state still shows
            // the link and a QR, so a blocked popup costs nothing.
            const opened = window.open(setup.launch_url, '_blank');
            if (opened) opened.opener = null;
        } catch (caught) {
            setError(surfaceErrorMessage(caught, 'Couldn’t start Telegram setup.'));
        }
    };

    const handleCreate = async () => {
        setError(null);
        const mode: SurfaceIdentityMode = identityMode ?? defaultMode(catalogEntry);

        try {
            let boundAccountId: string | undefined;
            if (mode === 'CUSTOM') {
                boundAccountId = usesJourney
                    ? await connectAccount({
                          connectorId: catalogEntry?.connector_id ?? definition.platform.toLowerCase(),
                          kind: catalogEntry?.kind,
                          credentials,
                      })
                    : accountId;
            }

            const created = (await createSurface.mutateAsync({
                podId,
                data: {
                    platform: definition.platform as SurfacePlatform,
                    name: newSurfaceName,
                    default_agent_name: agentName,
                    is_enabled: true,
                    credential_mode: mode as SurfaceCredentialMode,
                    ...(boundAccountId ? { account_id: boundAccountId } : {}),
                    config: { dm_conversation_reset_after_hours: DEFAULT_DM_RESET_HOURS },
                },
            })) as AssistantSurface;

            setCreatedSurface(created);
            // A surface Lemma can't wire up itself isn't reachable yet, so the
            // proof state would be a lie — go straight to what's left to do.
            // The setup read resolves against the surface we just created.
            setStep(
                mode === 'CUSTOM' && !definition.capabilities.autoWebhook ? 'setup' : 'live',
            );
        } catch (caught) {
            const conflict = parseCredentialConflict(caught);
            if (conflict) {
                // The catalog now knows who holds the claim; refetching makes the
                // option disable itself instead of us patching state by hand.
                void queryClient.invalidateQueries({ queryKey: ['pod-available-surfaces', podId] });
                setError(conflict.message);
                if (definition.identityOptions) setStep('identity');
                return;
            }
            setError(surfaceErrorMessage(caught, `Couldn’t connect ${definition.label}.`));
        }
    };

    /** Point an existing surface at an account the current user owns.
     *
     * The repair when the account behind a surface expires or its owner leaves:
     * the surface, its routes and its history stay exactly as they are — only
     * the credential underneath changes hands. */
    const handleRebind = async () => {
        if (!existingSurface) return;
        setError(null);
        try {
            const boundAccountId = usesJourney
                ? await connectAccount({
                      connectorId: catalogEntry?.connector_id ?? definition.platform.toLowerCase(),
                      kind: catalogEntry?.kind,
                      credentials,
                  })
                : accountId;

            await updateSurface.mutateAsync({
                podId,
                surfaceName: existingSurface.name,
                data: {
                    account_id: boundAccountId,
                    credential_mode: 'CUSTOM' as SurfaceCredentialMode,
                },
            });
            toast.success(`${definition.label} now runs on your account`);
            setRebinding(false);
            setStep(definition.capabilities.autoWebhook ? 'live' : 'setup');
        } catch (caught) {
            setError(surfaceErrorMessage(caught, `Couldn’t move ${definition.label} to your account.`));
        }
    };

    const handleSave = async () => {
        if (!existingSurface) return;
        setError(null);
        const config: SurfaceBehaviorConfigInput = {
            dm_conversation_reset_after_hours: DEFAULT_DM_RESET_HOURS,
            ...(definition.capabilities.channelRoutes
                ? {
                      channels: draft.channels
                          .filter((route) => route.channel_id)
                          .map((route) => ({
                              channel_id: route.channel_id,
                              channel_name: route.channel_name || null,
                              // Sent apart, never derived from an empty name:
                              // "the pod assistant answers here" and "nobody has
                              // said" both leave agent_name null, and the API
                              // rejects a route that claims to be both.
                              agent_name: route.use_pod_assistant ? null : route.agent_name,
                              use_pod_assistant: route.use_pod_assistant,
                          })),
                  }
                : {}),
            ...(definition.capabilities.senderFilters
                ? {
                      identity: {
                          allowed_domains: parseList(draft.allowedDomains),
                          allowed_email_addresses: parseList(draft.allowedEmails),
                      },
                  }
                : {}),
            send_policy: { allow_send: draft.allowSend },
        };

        try {
            await updateSurface.mutateAsync({
                podId,
                surfaceName: existingSurface.name,
                data: {
                    default_agent_name:
                        draft.agentName === DEFAULT_AGENT_VALUE ? null : draft.agentName,
                    config,
                },
            });
            toast.success(`${definition.label} updated`);
            onClose();
        } catch (caught) {
            setError(surfaceErrorMessage(caught, `Couldn’t save ${definition.label}.`));
        }
    };

    const handleSend = async () => {
        if (!existingSurface || !messageUserId || !messageBody.trim()) return;
        setError(null);
        try {
            const result = await sendMessage.mutateAsync({
                podId,
                surfaceName: existingSurface.name,
                userId: messageUserId,
                message: messageBody.trim(),
            });
            if (result?.sent) {
                toast.success('Message sent');
            } else {
                toast.warning('That member has no reachable thread here yet');
            }
            setMessageBody('');
            setStep('configure');
        } catch (caught) {
            setError(surfaceErrorMessage(caught, 'Couldn’t send that message.'));
        }
    };

    const handleRemove = async () => {
        if (!existingSurface) return;
        try {
            await deleteSurface.mutateAsync({ podId, surfaceName: existingSurface.name });
            toast.success(`${definition.label} disconnected`);
            onClose();
        } catch (caught) {
            setError(surfaceErrorMessage(caught, `Couldn’t remove ${definition.label}.`));
        }
    };

    const primary = primaryAction({
        step,
        identityMode,
        rebinding,
        onContinue: () => setStep('connect'),
        onCreate: handleCreate,
        onRebind: handleRebind,
        onStartManaged: handleStartManagedSetup,
        onSave: handleSave,
        onSend: handleSend,
        onClose,
    });
    const canGoBack =
        (step === 'connect' && (rebinding || Boolean(definition.identityOptions)))
        || step === 'message';
    const status = existingSurface ? getSurfaceStatus(existingSurface) : null;

    return (
        <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
            <DialogContent className={cn('surface-modal', step === 'configure' && 'is-wide')}>
                <DialogHeader className="surface-modal-header">
                    <div className="flex min-w-0 items-center gap-2.5">
                        <PlatformMark platform={definition.platform} />
                        <DialogTitle className="min-w-0 truncate text-base font-medium">
                            {definition.label}
                        </DialogTitle>
                        {status ? (
                            <span className={cn('chip chip-sm shrink-0', statusChipClass(status.tone))}>
                                {status.label}
                            </span>
                        ) : null}
                        {existingSurface ? (
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button
                                        type="button"
                                        variant="quiet"
                                        size="icon"
                                        className="ml-auto mr-7 h-7 w-7 rounded"
                                        aria-label="More actions"
                                    >
                                        <MoreHorizontal className="h-4 w-4" />
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                    {existingSurface.status === 'ACTIVE' ? (
                                        <DropdownMenuItem onSelect={() => setStep('message')}>
                                            <Send className="mr-2 h-4 w-4" />
                                            Message a member
                                        </DropdownMenuItem>
                                    ) : null}
                                    <DropdownMenuItem
                                        onSelect={() => void handleRemove()}
                                        className="text-[var(--state-error)]"
                                    >
                                        <Trash2 className="mr-2 h-4 w-4" />
                                        Disconnect {definition.label}
                                    </DropdownMenuItem>
                                </DropdownMenuContent>
                            </DropdownMenu>
                        ) : null}
                    </div>
                    <DialogDescription className="surface-modal-promise">
                        {rebinding && step === 'connect'
                            ? `Keep ${definition.label} exactly as it is, running on an account you own.`
                            : stepPromise(step, definition.promise, agentName, definition.label, setupKind, definition.capabilities.senderFilters)}
                    </DialogDescription>
                </DialogHeader>

                <div className="surface-modal-body">
                    {step === 'identity' ? (
                        <SurfaceIdentityStep
                            definition={definition}
                            catalog={catalogEntry}
                            agentName={agentName}
                            value={identityMode}
                            onChange={setIdentityMode}
                        />
                    ) : null}

                    {step === 'connect' && isSystemMode ? (
                        <p className="text-sm leading-6 text-[var(--text-secondary)]">
                            {forAgent(
                                'Lemma sets this up and points it at {agent}. Nothing to connect.',
                                agentName,
                            )}
                        </p>
                    ) : null}

                    {step === 'connect' && !isSystemMode ? (
                        <SurfaceConnectStep
                            definition={definition}
                            catalog={catalogEntry}
                            accounts={platformAccounts}
                            accountId={accountId}
                            onAccountChange={setAccountId}
                            credentials={credentials}
                            onCredentialsChange={setCredentials}
                            podId={podId}
                        />
                    ) : null}

                    {step === 'provisioning' ? (
                        <SurfaceProvisioningStep
                            setup={managedSetup}
                            launchUrl={launchUrl}
                            hasError={managedSetupFailed}
                            onRetry={() => {
                                setSetupId(null);
                                setLaunchUrl(null);
                                setStep('identity');
                            }}
                        />
                    ) : null}

                    {step === 'live' && existingSurface ? (
                        <SurfaceLiveStep definition={definition} surface={existingSurface} />
                    ) : null}

                    {step === 'setup' && existingSurface ? (
                        isLoadingSetup ? (
                            <p className="surface-verdict">
                                <StepLoader size="xs" /> Checking what’s left…
                            </p>
                        ) : hasSetupReference ? (
                            // Renders whenever there is anything to show, which
                            // includes reference-only cards — those are reached
                            // deliberately from settings rather than pushed here.
                            <div className="grid gap-4">
                                <SurfaceSetupChecklist actions={setupActions} consentUrl={consentUrl} />
                                {hasOutstandingSetup ? (
                                    <button
                                        type="button"
                                        onClick={() => setStep('configure')}
                                        className="lemma-quiet-text-button w-fit text-xs font-medium text-[var(--text-secondary)] underline-offset-2 hover:underline"
                                    >
                                        Skip for now
                                    </button>
                                ) : null}
                            </div>
                        ) : (
                            // Nothing outstanding and nothing to reference (the
                            // account runs on Lemma's own app) — show the proof
                            // instead of an empty list.
                            <SurfaceLiveStep definition={definition} surface={existingSurface} />
                        )
                    ) : null}

                    {step === 'configure' && existingSurface ? (
                        <div className="grid gap-4">
                            {hasOutstandingSetup ? (
                                <button
                                    type="button"
                                    onClick={() => setStep('setup')}
                                    className="surface-inline-callout flex items-center gap-2 text-left text-sm text-[var(--text-primary)]"
                                >
                                    <AlertTriangle className="h-4 w-4 shrink-0 text-[var(--state-warning)]" />
                                    Messages won’t arrive until setup is finished.
                                </button>
                            ) : null}
                            <SurfaceConfigureStep
                                definition={definition}
                                surface={existingSurface}
                                assistants={assistants}
                                draft={draft}
                                onDraftChange={patchDraft}
                                availableChannels={availableChannels}
                                isLoadingChannels={isLoadingChannels}
                                defaultRouteAgent={agentName}
                                customAppHref={
                                    definition.platform === 'SLACK' && !usesOwnApp
                                        ? `/pod/${podId}/connectors`
                                        : undefined
                                }
                                onOpenReference={
                                    hasSetupReference ? () => setStep('setup') : undefined
                                }
                                onRebind={() => {
                                    setAccountId('');
                                    setCredentials({});
                                    setIdentityMode('CUSTOM');
                                    setError(null);
                                    setRebinding(true);
                                    setStep('connect');
                                }}
                            />
                        </div>
                    ) : null}

                    {step === 'message' ? (
                        <SurfaceMessageStep
                            podId={podId}
                            userId={messageUserId}
                            onUserIdChange={setMessageUserId}
                            message={messageBody}
                            onMessageChange={setMessageBody}
                        />
                    ) : null}

                    {error ? (
                        <p className="surface-modal-error">
                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            <span className="min-w-0">{error}</span>
                        </p>
                    ) : null}
                </div>

                <DialogFooter className="surface-modal-footer">
                    {canGoBack ? (
                        <Button
                            type="button"
                            variant="quiet"
                            size="sm"
                            onClick={() => {
                                // A rebind entered `connect` from the settings
                                // step, so Back belongs there — not in the
                                // identity step this surface never revisits.
                                if (rebinding) {
                                    setRebinding(false);
                                    setStep('configure');
                                    return;
                                }
                                setStep(step === 'connect' ? 'identity' : 'configure');
                            }}
                            disabled={isBusy}
                            className="mr-auto"
                        >
                            <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
                            Back
                        </Button>
                    ) : null}
                    {blocker ? <span className="surface-modal-blocker">{blocker}</span> : null}
                    {step === 'live' ? null : (
                        <Button type="button" variant="secondary" onClick={onClose} disabled={isBusy}>
                            {step === 'provisioning' ? 'Close' : 'Cancel'}
                        </Button>
                    )}
                    {primary ? (
                        <Button variant="primary"
                            type="button"
                            onClick={() => void primary.run()}
                            disabled={isBusy || Boolean(blocker)}
                        >
                            {isBusy ? <StepLoader size="sm" className="mr-2" /> : null}
                            {primary.label}
                        </Button>
                    ) : null}
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

function primaryAction({
    step,
    identityMode,
    rebinding,
    onContinue,
    onCreate,
    onRebind,
    onStartManaged,
    onSave,
    onSend,
    onClose,
}: {
    step: SurfaceModalStep;
    identityMode: SurfaceIdentityMode | null;
    rebinding: boolean;
    onContinue: () => void;
    onCreate: () => Promise<void>;
    onRebind: () => Promise<void>;
    onStartManaged: () => Promise<void>;
    onSave: () => Promise<void>;
    onSend: () => Promise<void>;
    onClose: () => void;
}): { label: string; run: () => void | Promise<void> } | null {
    switch (step) {
        case 'identity':
            // Both Lemma-managed paths leave nothing to collect, so the verb acts
            // outright — one tap, as each option promises. Only bringing your own
            // account needs a further step.
            if (identityMode === 'SYSTEM') return { label: 'Connect', run: onCreate };
            if (identityMode === 'MANAGED') return { label: 'Create my bot', run: onStartManaged };
            return { label: 'Continue', run: onContinue };
        case 'connect':
            // Same state, different promise: nothing is being created here, an
            // existing surface is changing hands.
            return rebinding
                ? { label: 'Move to my account', run: onRebind }
                : { label: 'Connect', run: onCreate };
        // The work is happening in Telegram; offering a verb here would only
        // invite someone to click something that does nothing.
        case 'provisioning':
            return null;
        case 'live':
        case 'setup':
            return { label: 'Done', run: onClose };
        case 'message':
            return { label: 'Send message', run: onSend };
        default:
            return { label: 'Save', run: onSave };
    }
}

/** What the setup state is actually for, which decides how it introduces
 * itself: work outstanding, a reference to check, or nothing at all. */
type SetupKind = 'blocking' | 'reference' | 'none';

function stepPromise(
    step: SurfaceModalStep,
    promise: string,
    agentName: string | null,
    label: string,
    setupKind: SetupKind,
    senderFilters: boolean,
): string {
    const reachable = forAgent(`${label} can reach {agent} now. Here’s the address.`, agentName);
    if (step === 'live') return reachable;
    if (step === 'setup') {
        if (setupKind === 'blocking') return `Two minutes in ${label}, then messages start arriving.`;
        if (setupKind === 'reference') return `Where ${label} delivers, if you ever need to check.`;
        return reachable;
    }
    if (step === 'provisioning') return 'Finish naming it in Telegram — the bot is yours.';
    // "What gets through" is only true where senders can be filtered — a
    // mailbox. On Slack and Teams there is nothing to filter, so promising it
    // described a screen that doesn't exist.
    if (step === 'configure') {
        return senderFilters
            ? forAgent('Who answers as {agent}, and whose messages become work.', agentName)
            : forAgent('Who answers as {agent}, and where.', agentName);
    }
    if (step === 'message') return forAgent('Reach a teammate as {agent}.', agentName);
    return forAgent(promise, agentName);
}

function statusChipClass(tone: string): string {
    if (tone === 'success') return 'state-badge-success';
    if (tone === 'warning') return 'state-badge-warning';
    if (tone === 'danger') return 'state-badge-error';
    return 'chip-muted';
}

function defaultMode(entry: Parameters<typeof hasSystemIdentity>[0]): SurfaceIdentityMode {
    // No fork to offer: run on the Lemma-managed identity when this deployment
    // has one (Resend), otherwise an account is the only way in.
    return hasSystemIdentity(entry) ? 'SYSTEM' : 'CUSTOM';
}

/** An unfilled route row. The channel is picked in the modal; the agent is not,
 * because the page that opened it already answered that. */
function blankChannelRow(agentName: string | null) {
    return {
        channel_id: '',
        channel_name: '',
        agent_name: agentName,
        use_pod_assistant: agentName === null,
    };
}

function emptyDraft(): ConfigureDraft {
    return {
        agentName: DEFAULT_AGENT_VALUE,
        channels: [],
        allowedDomains: '',
        allowedEmails: '',
        allowSend: false,
    };
}

function draftFromSurface(surface: AssistantSurface): ConfigureDraft {
    const config = surface.config || {};
    const identity = config.identity || {};
    return {
        agentName: surface.agent_name || DEFAULT_AGENT_VALUE,
        channels: (config.channels || []).map((route) => ({
            channel_id: route.channel_id || '',
            channel_name: route.channel_name || '',
            agent_name: route.agent_name ?? null,
            use_pod_assistant: Boolean(route.use_pod_assistant),
        })),
        allowedDomains: (identity.allowed_domains || []).join(', '),
        allowedEmails: (identity.allowed_email_addresses || []).join(', '),
        allowSend: Boolean(
            (config as { send_policy?: { allow_send?: boolean } }).send_policy?.allow_send,
        ),
    };
}

function parseList(raw: string): string[] {
    const out: string[] = [];
    for (const token of raw.split(/[\s,;]+/)) {
        const value = token.trim().toLowerCase();
        if (value && !out.includes(value)) out.push(value);
    }
    return out;
}

/** Accounts are matched on the connector the catalog names for the platform, so
 * a Gmail surface never offers a Calendar account that shares the address. */
function accountMatchesConnector(account: Account, connectorId: string | undefined): boolean {
    if (!connectorId) return false;
    const target = connectorId.toLowerCase();
    return [account.connector_id, account.connector?.name, account.connector?.title].some(
        (value) => String(value ?? '').toLowerCase() === target,
    );
}
