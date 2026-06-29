'use client';

import { use, useEffect, useMemo, useRef } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowLeft, PanelLeftOpen, Plus, XCircle } from 'lucide-react';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { PodAssistantEmbedded } from '@/components/ai/pod-assistant';
import { resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { InlineLoader } from '@/components/brand/loader';
import { usePodLayout } from '@/components/pod/pod-layout-context';
import { Button } from '@/components/ui/button';
import { useAgentRuntimes, useAvailableAgentRuntimeHarnesses } from '@/lib/hooks/use-agent-runtime';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import type { AgentRuntimeConfig } from '@/lib/types';
import { cn } from '@/lib/utils';
import { getConversationStatusView, type ConversationStatusView } from '@/lib/utils/conversations';

function ConversationStatusPill({ statusView }: { statusView: ConversationStatusView }) {
    if (statusView.state === 'unknown' || statusView.state === 'completed') return null;

    return (
        <span
            className={cn(
                'inline-flex h-6 shrink-0 items-center gap-1.5 rounded-full border px-2 text-xs font-medium',
                statusView.tone === 'live' && 'state-badge-brand',
                statusView.tone === 'warning' && 'state-badge-warning',
                statusView.tone === 'danger' && 'state-badge-error',
                statusView.tone === 'muted' && 'chip-muted'
            )}
        >
            {statusView.state === 'failed' ? (
                <XCircle className="h-3 w-3" strokeWidth={1.9} />
            ) : (
                <span
                    className={cn(
                        'h-1.5 w-1.5 rounded-full bg-current',
                        statusView.isActive && 'animate-pulse'
                    )}
                />
            )}
            {statusView.label}
        </span>
    );
}

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
    const { isCompact, toggleNav } = usePodLayout();
    const podAccess = usePodAccess(podId);
    const { data: pod } = usePod(podId);
    const { data: runtimeCatalog } = useAgentRuntimes(pod?.organization_id);
    const { data: availableHarnesses } = useAvailableAgentRuntimeHarnesses();
    const {
        activeConversationId,
        clearMessages,
        closeAssistant,
        isReady,
        selectConversation,
        sendMessage,
    } = assistant;
    const assistantMessage = searchParams.get('assistantMessage');
    const conversationInstructions = searchParams.get('conversationInstructions');
    const conversationMetadata = useMemo(
        () => parseConversationMetadata(searchParams.get('conversationMetadata')),
        [searchParams]
    );
    const isNewConversation = conversationId === 'new';
    const newRouteInitializedRef = useRef(false);
    const ignoredConversationIdAfterNewRef = useRef<string | null>(null);
    const activeConversationIdRef = useRef<string | null>(activeConversationId);
    const handledAssistantMessageRef = useRef<string | null>(null);
    const activeConversation = useMemo(() => {
        const resolvedConversationId = isNewConversation ? null : conversationId;
        if (!resolvedConversationId) return null;
        return assistant.conversations.find((conversation) => conversation.id === resolvedConversationId) ?? null;
    }, [assistant.conversations, conversationId, isNewConversation]);
    const conversationTitle = isNewConversation
        ? 'New conversation'
        : activeConversation?.title?.trim() || 'Untitled conversation';
    const activeStatusView = getConversationStatusView(activeConversation?.status);
    const isRouteConversationSelected = isNewConversation || activeConversationId === conversationId;
    const isSelectingRouteConversation = !isNewConversation && activeConversationId !== conversationId;
    const canWriteConversations = podAccess.can('conversation.write');
    const podDefaultRuntime = pod?.config?.default_runtime
        ?? resolveDefaultAgentRuntime(runtimeCatalog, pod?.config?.default_profile_id, availableHarnesses);
    const selectedCommandRuntime = assistant.conversationRuntime ?? null;
    const handleCommandRuntimeChange = (runtime: AgentRuntimeConfig | null) => {
        void assistant.setConversationModel((runtime?.model_name ?? null) as never, runtime);
    };
    const composerModelControl = isNewConversation ? (
        <RuntimeModelPicker
            catalog={runtimeCatalog}
            availableHarnesses={availableHarnesses}
            defaultRuntime={podDefaultRuntime}
            value={selectedCommandRuntime}
            onChange={handleCommandRuntimeChange}
            disabled={!canWriteConversations}
            compact
            scopeHint="Just for this chat"
            manageHref={pod?.organization_id ? `/organizations/${pod.organization_id}/settings/agent-runtimes` : undefined}
        />
    ) : undefined;

    useEffect(() => {
        activeConversationIdRef.current = activeConversationId;
    }, [activeConversationId]);

    useEffect(() => {
        closeAssistant({ suppressUrlRestore: false });
        if (isNewConversation) {
            if (!newRouteInitializedRef.current) {
                ignoredConversationIdAfterNewRef.current = activeConversationIdRef.current;
                clearMessages();
                newRouteInitializedRef.current = true;
            }
            return;
        }
        newRouteInitializedRef.current = false;
        ignoredConversationIdAfterNewRef.current = null;
        if (activeConversationId !== conversationId) {
            selectConversation(conversationId);
        }
    }, [activeConversationId, clearMessages, closeAssistant, conversationId, isNewConversation, selectConversation]);

    useEffect(() => {
        if (assistantMessage) return;
        if (!isNewConversation || !activeConversationId) return;
        if (activeConversationId === ignoredConversationIdAfterNewRef.current) return;
        router.replace(`/pod/${podId}/conversations/${encodeURIComponent(activeConversationId)}`);
    }, [activeConversationId, assistantMessage, isNewConversation, podId, router]);

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
            ignoredConversationIdAfterNewRef.current = activeConversationIdRef.current;
            newRouteInitializedRef.current = true;
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
    }, [assistantMessage, clearMessages, closeAssistant, conversationInstructions, conversationMetadata, isNewConversation, isReady, podId, router, searchParams, sendMessage]);

    const startNewConversation = () => {
        router.push(`/pod/${podId}/conversations/new`);
    };

    return (
        <div className="flex h-full min-h-0 flex-col bg-[var(--pod-main-bg)]">
            <header className="pod-shell-topbar flex h-14 shrink-0 items-center px-4 sm:px-6 lg:px-8">
                <div className="flex h-8 w-full items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2 text-sm">
                        {isCompact ? (
                            <button
                                type="button"
                                onClick={toggleNav}
                                className="lemma-shell-icon-button custom-focus-ring h-8 w-8 shrink-0 text-[var(--text-tertiary)]"
                                aria-label="Open navigation"
                                title="Open navigation"
                            >
                                <PanelLeftOpen className="h-4 w-4" strokeWidth={1.8} />
                            </button>
                        ) : null}
                        <Link
                            href={`/pod/${podId}/conversations`}
                            className="custom-focus-ring inline-flex h-8 shrink-0 items-center gap-1 rounded-md px-1.5 leading-none text-[var(--text-tertiary)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
                        >
                            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.8} />
                            <span className="hidden sm:inline">Conversations</span>
                        </Link>
                        <h1 className="min-w-0 truncate text-sm font-medium leading-none text-[var(--text-primary)]">
                            {conversationTitle}
                        </h1>
                        <ConversationStatusPill statusView={activeStatusView} />
                    </div>
                    <Button type="button" variant="ghost" size="sm" className="h-8 shrink-0 gap-1.5 px-2 text-sm text-[var(--text-secondary)]" onClick={startNewConversation}>
                        <Plus className="h-3.5 w-3.5" />
                        New
                    </Button>
                </div>
            </header>

            <section className="min-h-0 flex-1">
                {isRouteConversationSelected ? (
                    <PodAssistantEmbedded
                        title={conversationTitle}
                        subtitle=""
                        placeholder="Message"
                        showHeader={false}
                        showModelPicker={false}
                        composerModelControl={composerModelControl}
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
}
