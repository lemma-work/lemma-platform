'use client';

import { use, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { PodAssistantEmbedded } from '@/components/ai/pod-assistant';
import { resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { InlineLoader } from '@/components/brand/loader';
import { ConversationComposerContext } from '@/components/conversations/conversation-composer-context';
import { PodNewWorkspace } from '@/components/pod/pod-new-workspace';
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
import { useAgentRuntimes, useAvailableAgentRuntimeHarnesses } from '@/lib/hooks/use-agent-runtime';
import { useAgent, useAgents } from '@/lib/hooks/use-agents';
import { useConversation } from '@/lib/hooks/use-assistants';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
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

function parseConversationMetadata(value: string | null): Record<string, unknown> | null {
    if (!value) return null;
    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
    } catch {
        return null;
    }
}

export default function PodConversationPage({
    params,
}: {
    params: Promise<{ id: string; conversationId: string }>;
}) {
    const { id: podId, conversationId } = use(params);
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
    const { data: availableHarnesses } = useAvailableAgentRuntimeHarnesses();
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
    const scopedAgentName = searchParams.get('agent')?.trim() || null;
    const presentedResourceHref = normalizeConversationPresentedResourceHref(
        searchParams.get('presented'),
        podId,
    );
    const conversationInstructions = searchParams.get('conversationInstructions');
    const conversationMetadata = useMemo(
        () => parseConversationMetadata(searchParams.get('conversationMetadata')),
        [searchParams]
    );
    const isNewConversation = conversationId === 'new';
    const newConversationScopeKey = scopedAgentName ?? '__pod_default__';
    const { data: fetchedConversation } = useConversation(
        podId,
        isNewConversation ? '' : conversationId,
    );
    const newRouteScopeRef = useRef<string | null>(null);
    const ignoredConversationIdAfterNewRef = useRef<string | null>(null);
    const openedConversationIdRef = useRef<string | null>(openedConversationId);
    const handledAssistantMessageRef = useRef<string | null>(null);
    const listedConversation = useMemo(() => {
        const resolvedConversationId = isNewConversation ? null : conversationId;
        if (!resolvedConversationId) return null;
        return assistant.conversations.find((conversation) => conversation.id === resolvedConversationId) ?? null;
    }, [assistant.conversations, conversationId, isNewConversation]);
    const activeConversation = listedConversation ?? fetchedConversation ?? null;
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
    const isSelectingRouteConversation = !isNewConversation && openedConversationId !== conversationId;
    const canWriteConversations = podAccess.can('conversation.write');
    const podDefaultRuntime = pod?.config?.default_runtime
        ?? resolveDefaultAgentRuntime(runtimeCatalog, pod?.config?.default_profile_id, availableHarnesses);
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
    const handleCommandRuntimeChange = (runtime: AgentRuntimeConfig | null) => {
        setRuntimeOverride(runtime);
        void setConversationModel((runtime?.model_name ?? null) as never, runtime)
            .catch(() => setRuntimeOverride(undefined));
    };
    const handleAgentChange = (agentName: string | null) => {
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
    };
    const composerContextControl = (isNewConversation || activeConversation) ? (
        <ConversationComposerContext
            agents={agents}
            selectedAgentName={selectedAgentName}
            agentDisplayLabel={!isNewConversation && activeConversation?.agent_id && !selectedAgentName ? 'Agent' : undefined}
            selectedRuntime={selectedCommandRuntime}
            defaultRuntime={effectiveDefaultRuntime}
            runtimeCatalog={runtimeCatalog}
            availableHarnesses={availableHarnesses}
            isNewConversation={isNewConversation}
            canWrite={canWriteConversations}
            onAgentChange={handleAgentChange}
            onRuntimeChange={handleCommandRuntimeChange}
            manageModelsHref={pod?.organization_id ? `/organizations/${pod.organization_id}/settings/agent-runtimes` : undefined}
        />
    ) : undefined;
    const closePresentedResource = () => {
        const nextQuery = removeConversationPresentationParam(searchParamsString);
        router.replace(
            `/pod/${podId}/conversations/${encodeURIComponent(conversationId)}${nextQuery ? `?${nextQuery}` : ''}`,
            { scroll: false },
        );
    };

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
            const nextParams = new URLSearchParams(searchParams.toString());
            nextParams.delete('assistantMessage');
            nextParams.delete('conversationInstructions');
            nextParams.delete('conversationMetadata');
            const nextQuery = nextParams.toString();
            router.replace(`/pod/${podId}/conversations/new${nextQuery ? `?${nextQuery}` : ''}`);
        })();
    }, [assistantMessage, clearMessages, closeAssistant, conversationInstructions, conversationMetadata, isNewConversation, isReady, newConversationScopeKey, podId, router, searchParams, sendMessage]);

    if (isNewConversation) {
        return (
            <div className="h-full min-h-0 bg-[var(--pod-main-bg)]">
                <PodAssistantEmbedded
                    title="New"
                    subtitle=""
                    placeholder="Message"
                    showHeader={false}
                    showModelPicker={false}
                    composerModelControl={composerContextControl}
                    showNewConversationButton={false}
                    density="spacious"
                    contentWidthClassName="!max-w-4xl"
                    composerWidthClassName="!max-w-4xl"
                    className="h-full rounded-none border-0 bg-transparent shadow-none"
                    draft={newWorkspaceDraft}
                    onDraftChange={setNewWorkspaceDraft}
                    emptyState={(
                        <PodNewWorkspace
                            podId={podId}
                            onPreparePrompt={setNewWorkspaceDraft}
                        />
                    )}
                />
            </div>
        );
    }

    const conversationSurface = (
        <div className="flex h-full min-h-0 flex-col bg-[var(--pod-main-bg)]">
            <section className="min-h-0 flex-1">
                {isRouteConversationSelected ? (
                    <PodAssistantEmbedded
                        title={conversationTitle}
                        subtitle=""
                        placeholder="Message"
                        showHeader={false}
                        showModelPicker={false}
                        composerModelControl={composerContextControl}
                        showNewConversationButton={false}
                        density="spacious"
                        contentWidthClassName="!max-w-4xl"
                        composerWidthClassName="!max-w-4xl"
                        className="h-full rounded-none border-0 bg-transparent shadow-none"
                    />
                ) : (
                    <div className="flex h-full min-h-0 items-center justify-center px-6">
                        <InlineLoader
                            size="sm"
                            label="Loading conversation"
                            className={isSelectingRouteConversation ? "animate-in fade-in duration-200" : undefined}
                        />
                    </div>
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
