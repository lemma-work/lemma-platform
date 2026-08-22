'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { PodAssistantEmbedded } from '@/components/ai/pod-assistant';
import { resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { ConversationComposerContext } from '@/components/conversations/conversation-composer-context';
import { projectFromMetadata } from '@/lib/assistant/project-selection';
import { PodNewWorkspace } from '@/components/pod/pod-new-workspace';
import { PodWelcome, type PodWelcomeChoice } from '@/components/pod/pod-welcome';
import { PodConversationSkeleton } from '@/components/pod/route-skeletons';
import { ConversationPresentationStage } from '@/components/pod/conversation-presentation-stage';
import {
    buildScopedConversationHref,
    resolveConversationAgentName,
    resolveHydratedConversationRuntime,
    updateConversationAgentQuery,
} from '@/lib/assistant/conversation-composer-context';
import {
    normalizeConversationPresentedResourceHref,
    removeConversationPresentationParam,
} from '@/lib/assistant/conversation-presentation';
import { useAgentRuntimes } from '@/lib/hooks/use-agent-runtime';
import { useAgent, useAgents } from '@/lib/hooks/use-agents';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { POD_WELCOME_PARAM, parseConversationMetadataParam, stripAssistantLaunchParams } from '@/lib/pods/composer-launch';
import { buildNewPodConversationHref } from '@/lib/pods/new-pod-conversation';
import { withSettingsReturnPath } from '@/lib/navigation/settings-return';
import type { AgentRuntimeConfig } from '@/lib/types';

function waitForConversationReset() {
    if (typeof window === 'undefined') {
        return Promise.resolve();
    }

    return new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(() => resolve());
        });
    });
}

/**
 * The conversation surface lives here, above the `[conversationId]` segment,
 * because Next keys a route by its concrete path: `/conversations/new` and
 * `/conversations/<id>` are two different pages, and the navigation between
 * them — the one every first message performs — unmounted the whole surface
 * and built it again. The transcript, the composer, the caret and the scroll
 * position all went with it, at the exact moment the reader was watching their
 * message land.
 *
 * A layout above the dynamic segment survives that navigation. `useParams()`
 * still reports the new id, so the surface re-renders with it rather than being
 * replaced by a copy of itself.
 */
export default function PodConversationsLayout({ children }: { children: ReactNode }) {
    const params = useParams();
    const podId = typeof params.id === 'string' ? params.id : '';
    const conversationId = typeof params.conversationId === 'string'
        ? params.conversationId
        : null;

    // The conversation list is a sibling route with none of this on it, so it
    // renders as itself. Only a conversation route mounts the surface — and
    // then one instance of it serves every conversation.
    if (!conversationId) {
        return <>{children}</>;
    }

    return <PodConversationSurface podId={podId} conversationId={conversationId} />;
}

function PodConversationSurface({
    podId,
    conversationId,
}: {
    podId: string;
    conversationId: string;
}) {
    const router = useRouter();
    const searchParams = useSearchParams();
    const assistant = useAIAssistant();
    const [newWorkspaceDraft, setNewWorkspaceDraft] = useState('');
    const [runtimeOverride, setRuntimeOverride] = useState<AgentRuntimeConfig | null | undefined>(undefined);
    const podAccess = usePodAccess(podId);
    const { data: pod } = usePod(podId);
    const canReadAgents = podAccess.can('agent.read');
    const { data: agentsData } = useAgents(canReadAgents ? podId : undefined);
    const { data: runtimeCatalog } = useAgentRuntimes(pod?.organization_id);
    const {
        openedConversationId,
        clearMessages,
        closeAssistant,
        isReady,
        openConversation,
        sendMessage,
        setConversationModel,
    } = assistant;
    const assistantMessage = searchParams.get('assistantMessage');
    const searchParamsString = searchParams.toString();
    const conversationRouteHref = `/pod/${podId}/conversations/${encodeURIComponent(conversationId)}${searchParamsString ? `?${searchParamsString}` : ''}`;
    const scopedAgentName = searchParams.get('agent')?.trim() || null;
    const presentedResourceHref = normalizeConversationPresentedResourceHref(
        searchParams.get('presented'),
        podId,
    );
    const conversationInstructions = searchParams.get('conversationInstructions');
    const conversationMetadata = useMemo(
        () => parseConversationMetadataParam(searchParams.get('conversationMetadata')),
        [searchParams]
    );
    const isNewConversation = conversationId === 'new';
    const newConversationScopeKey = scopedAgentName ?? '__pod_default__';
    const newWorkspaceRef = useRef<HTMLDivElement>(null);
    const newRouteScopeRef = useRef<string | null>(null);
    const ignoredConversationIdAfterNewRef = useRef<string | null>(null);
    const openedConversationIdRef = useRef<string | null>(openedConversationId);
    const handledAssistantMessageRef = useRef<string | null>(null);
    // Opening the route conversation makes the controller fetch it and keep it
    // in this list, so reading it from there is the same record a second
    // request would have returned — one route, one GET of the conversation.
    const activeConversation = useMemo(() => {
        const resolvedConversationId = isNewConversation ? null : conversationId;
        if (!resolvedConversationId) return null;
        return assistant.conversations.find((conversation) => conversation.id === resolvedConversationId) ?? null;
    }, [assistant.conversations, conversationId, isNewConversation]);
    const agents = useMemo(() => agentsData?.items ?? [], [agentsData?.items]);
    const persistedAgentName = useMemo(
        () => resolveConversationAgentName(activeConversation?.agent_id, agents),
        [activeConversation?.agent_id, agents],
    );
    const selectedAgentName = isNewConversation
        ? scopedAgentName
        : activeConversation?.agent_id
            ? persistedAgentName ?? scopedAgentName
            : null;
    const { data: selectedAgent } = useAgent(
        canReadAgents ? podId : undefined,
        selectedAgentName ?? undefined,
    );
    const conversationTitle = isNewConversation
        ? 'New conversation'
        : activeConversation?.title?.trim() || 'Untitled conversation';
    const isRouteConversationSelected = isNewConversation || openedConversationId === conversationId;
    const canWriteConversations = podAccess.can('conversation.write');
    const podDefaultRuntime = pod?.config?.default_runtime
        ?? resolveDefaultAgentRuntime(runtimeCatalog, pod?.config?.default_profile_id);
    const hydratedConversationRuntime = resolveHydratedConversationRuntime({
        isNewConversation,
        hasPersistedConversation: Boolean(activeConversation),
        persistedRuntime: activeConversation?.agent_runtime,
        controllerRuntime: assistant.conversationRuntime,
    });
    const selectedCommandRuntime = runtimeOverride !== undefined
        ? runtimeOverride
        : hydratedConversationRuntime;
    const effectiveDefaultRuntime = selectedAgent?.agent_runtime ?? podDefaultRuntime;
    const handleCommandRuntimeChange = useCallback((runtime: AgentRuntimeConfig | null) => {
        setRuntimeOverride(runtime);
        void setConversationModel((runtime?.model_name ?? null) as never, runtime)
            .catch(() => setRuntimeOverride(undefined));
    }, [setConversationModel]);
    // Handing the composer a prompt without the caret is a dead end — a starter
    // that leaves "Build an app that " sitting in an unfocused box asks the
    // reader to click twice to finish a sentence we started.
    const prepareWorkspacePrompt = useCallback((prompt: string) => {
        setNewWorkspaceDraft(prompt);
        window.requestAnimationFrame(() => {
            const textarea = newWorkspaceRef.current?.querySelector<HTMLTextAreaElement>('.assistant-composer-textarea');
            if (!textarea) return;
            textarea.focus();
            textarea.setSelectionRange(prompt.length, prompt.length);
        });
    }, []);
    const handleAgentChange = useCallback((agentName: string | null) => {
        setRuntimeOverride(undefined);
        ignoredConversationIdAfterNewRef.current = openedConversationIdRef.current;
        clearMessages();
        void setConversationModel(null, null);
        newRouteScopeRef.current = agentName?.trim() || '__pod_default__';
        const nextQuery = updateConversationAgentQuery(searchParamsString, agentName);
        router.replace(
            `/pod/${podId}/conversations/new${nextQuery ? `?${nextQuery}` : ''}`,
            { scroll: false },
        );
    }, [clearMessages, podId, router, searchParamsString, setConversationModel]);
    // Memoized elements, both of them, because they are handed to a memoized
    // transcript as props. Rebuilt inline they were a changed prop on every
    // keystroke in the composer, which is how typing on the new-conversation
    // screen re-rendered the launcher and the transcript with it.
    const composerContextControl = useMemo(() => (isNewConversation || activeConversation) ? (
        <ConversationComposerContext
            agents={agents}
            selectedAgentName={selectedAgentName}
            agentDisplayLabel={!isNewConversation && activeConversation?.agent_id && !selectedAgentName ? 'Agent' : undefined}
            selectedRuntime={selectedCommandRuntime}
            defaultRuntime={effectiveDefaultRuntime}
            runtimeCatalog={runtimeCatalog}
            isNewConversation={isNewConversation}
            canWrite={canWriteConversations}
            podId={podId}
            boundProject={projectFromMetadata(activeConversation?.metadata)}
            onAgentChange={handleAgentChange}
            onRuntimeChange={handleCommandRuntimeChange}
            manageModelsHref={pod?.organization_id
                ? withSettingsReturnPath(
                    `/organizations/${pod.organization_id}/settings/agent-runtimes`,
                    conversationRouteHref,
                )
                : undefined}
        />
    ) : undefined, [
        activeConversation,
        agents,
        canWriteConversations,
        conversationRouteHref,
        effectiveDefaultRuntime,
        handleAgentChange,
        handleCommandRuntimeChange,
        isNewConversation,
        pod,
        podId,
        runtimeCatalog,
        selectedAgentName,
        selectedCommandRuntime,
    ]);

    const newWorkspaceLauncher = useMemo(() => (
        <PodNewWorkspace
            podId={podId}
            selectedAgentName={selectedAgentName}
            onPreparePrompt={prepareWorkspacePrompt}
            onSelectAgent={handleAgentChange}
        />
    ), [handleAgentChange, podId, prepareWorkspacePrompt, selectedAgentName]);

    const closePresentedResource = useCallback(() => {
        const nextQuery = removeConversationPresentationParam(searchParamsString);
        router.replace(
            `/pod/${podId}/conversations/${encodeURIComponent(conversationId)}${nextQuery ? `?${nextQuery}` : ''}`,
            { scroll: false },
        );
    }, [conversationId, podId, router, searchParamsString]);

    useEffect(() => {
        openedConversationIdRef.current = openedConversationId;
    }, [openedConversationId]);

    useEffect(() => {
        closeAssistant({ suppressUrlRestore: false });
        if (isNewConversation) {
            if (newRouteScopeRef.current !== newConversationScopeKey) {
                ignoredConversationIdAfterNewRef.current = openedConversationIdRef.current;
                clearMessages();
                void setConversationModel(null, null);
                newRouteScopeRef.current = newConversationScopeKey;
            }
            return;
        }
        newRouteScopeRef.current = null;
        ignoredConversationIdAfterNewRef.current = null;
        if (openedConversationId !== conversationId) {
            openConversation(conversationId);
        }
    }, [clearMessages, closeAssistant, conversationId, isNewConversation, newConversationScopeKey, openConversation, openedConversationId, setConversationModel]);

    useEffect(() => {
        if (assistantMessage) return;
        if (!isNewConversation || !openedConversationId) return;
        if (openedConversationId === ignoredConversationIdAfterNewRef.current) return;
        router.replace(buildScopedConversationHref({
            podId,
            conversationId: openedConversationId,
            agentName: scopedAgentName,
        }));
    }, [assistantMessage, isNewConversation, openedConversationId, podId, router, scopedAgentName]);

    useEffect(() => {
        if (!isNewConversation || !assistantMessage || !isReady) return;

        const message = assistantMessage.trim();
        if (!message) return;

        const key = `${podId}:${message}:${conversationInstructions || ''}:${JSON.stringify(conversationMetadata || {})}`;
        if (handledAssistantMessageRef.current === key) return;
        handledAssistantMessageRef.current = key;

        // Before the send, not after it. The params are spent the moment the
        // message is dispatched, and waiting for the answer would leave a URL
        // that replays the whole send on reload for as long as the turn runs.
        const nextQuery = stripAssistantLaunchParams(searchParams);
        router.replace(`/pod/${podId}/conversations/new${nextQuery ? `?${nextQuery}` : ''}`);

        void (async () => {
            closeAssistant({ suppressUrlRestore: false });
            clearMessages();
            ignoredConversationIdAfterNewRef.current = openedConversationIdRef.current;
            newRouteScopeRef.current = newConversationScopeKey;
            await waitForConversationReset();
            await sendMessage(message, {
                forceNewConversation: true,
                instructions: conversationInstructions || undefined,
                conversationMetadata: conversationMetadata ?? undefined,
                metadata: {
                    source: typeof conversationMetadata?.source === 'string'
                        ? conversationMetadata.source
                        : 'onboarding_start',
                },
            });
        })();
    }, [assistantMessage, clearMessages, closeAssistant, conversationInstructions, conversationMetadata, isNewConversation, isReady, newConversationScopeKey, podId, router, searchParams, sendMessage]);

    /**
     * A pod that was just created arrives here having said nothing, so the door
     * asks before anything is sent. `assistantMessage` in the URL means the
     * asking is over — either the door was answered, or the arrival stated its
     * intent somewhere else — and the effect above is already sending.
     */
    const showWelcome = isNewConversation
        && searchParams.get(POD_WELCOME_PARAM) === '1'
        && !assistantMessage;
    const isFirstPod = conversationMetadata?.first_run === true;

    /**
     * Answering the door is a `replace` back onto this same route carrying an
     * `assistantMessage`, which the effect above then sends. Nothing new sends
     * a message: the door only decides which sentence, and with what framing.
     *
     * `null` is the way past — no opening message means the greeting and the
     * welcome turn, which is exactly what this route did before the door.
     */
    const leaveWelcome = (choice: PodWelcomeChoice | null) => {
        router.replace(buildNewPodConversationHref({
            podId,
            podName: pod?.name ?? '',
            workDomain: typeof conversationMetadata?.work_domain === 'string'
                ? conversationMetadata.work_domain
                : null,
            isFirstPod,
            openingMessage: choice?.message,
            extraInstructions: choice?.instructions,
            metadata: {
                ...conversationMetadata,
                welcome_choice: choice ? choice.optionId ?? 'own_words' : 'skipped',
            },
        }));
    };

    // One tree for both states, deliberately. This component is now the same
    // instance before and after the conversation exists, so what differs
    // between "new" and "open" has to be props — swapping the element type here
    // would hand back the remount the layout was moved up to avoid.
    const conversationSurface = (
        <div ref={newWorkspaceRef} className="flex h-full min-h-0 flex-col bg-[var(--pod-main-bg)]">
            {/* Gated on the pod having loaded, because answering the door
                builds instructions that name it — a click that landed a beat
                early would tell the agent the pod is called nothing. */}
            {showWelcome && pod ? (
                <PodWelcome
                    onStart={leaveWelcome}
                    onSkip={() => leaveWelcome(null)}
                />
            ) : null}
            <section className="min-h-0 flex-1">
                {isRouteConversationSelected ? (
                    <PodAssistantEmbedded
                        title={isNewConversation ? 'New' : conversationTitle}
                        subtitle=""
                        placeholder="Message"
                        showHeader={false}
                        showModelPicker={false}
                        composerModelControl={composerContextControl}
                        showNewConversationButton={false}
                        density="spacious"
                        contentWidthClassName="!max-w-3xl"
                        composerWidthClassName="!max-w-3xl"
                        className="h-full rounded-none border-0 bg-transparent shadow-none"
                        // Lifted only while the launcher is on screen, because
                        // that is the only thing that writes the draft from
                        // outside the composer. The handover is silent: the
                        // draft is empty at the moment of the swap, having just
                        // been sent.
                        draft={isNewConversation ? newWorkspaceDraft : undefined}
                        onDraftChange={isNewConversation ? setNewWorkspaceDraft : undefined}
                        emptyStateFillsViewport={isNewConversation}
                        emptyState={isNewConversation ? newWorkspaceLauncher : undefined}
                    />
                ) : (
                    /* The gate is about *which* conversation's messages, not
                       whether any are coming: until `openConversation` lands, the
                       controller still holds the previous conversation's
                       transcript, and showing that would be worse than showing
                       nothing. It is the same component the route boundary just
                       rendered — a sequence of waits is still one load to the
                       reader, so the handover must not be a second screen. */
                    <PodConversationSkeleton />
                )}
            </section>
        </div>
    );

    if (presentedResourceHref) {
        return (
            <ConversationPresentationStage
                podId={podId}
                resourceHref={presentedResourceHref}
                onClose={closePresentedResource}
            >
                {conversationSurface}
            </ConversationPresentationStage>
        );
    }

    return conversationSurface;
}
