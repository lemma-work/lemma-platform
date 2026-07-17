'use client';

import { use, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Check, Loader2 } from '@/components/ui/icons';

import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { InlineWidget } from '@/components/lemma/assistant/inline-widget';
import {
    CONVERSATION_STAGE_EMBED_PARAM,
    CONVERSATION_STAGE_EMBED_VALUE,
} from '@/lib/assistant/conversation-presentation';

function isHttpUrl(value: string | null): string | null {
    if (!value) return null;
    try {
        const url = new URL(value);
        return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null;
    } catch {
        return null;
    }
}

export default function DisplayResourceWidgetPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const searchParams = useSearchParams();
    const conversationId = searchParams.get('assistantConversationId') || searchParams.get('conversationId');
    const toolCallId = searchParams.get('toolCallId');
    const externalSrc = isHttpUrl(searchParams.get('src'));
    const isConversationStage =
        searchParams.get(CONVERSATION_STAGE_EMBED_PARAM) === CONVERSATION_STAGE_EMBED_VALUE;
    const isStandalone = searchParams.get('standalone') === '1';
    const loadingMessages = searchParams.getAll('loadingMessage')
        .map((message) => message.trim())
        .filter(Boolean)
        .slice(0, 4);
    const assistant = useAIAssistant();
    const initializedAssistantConversationRef = useRef<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [savedAppUrl, setSavedAppUrl] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);

    const title = 'Widget';
    const isContentWidget = !externalSrc;

    const openedConversationId = assistant.openedConversationId;
    const openAssistant = assistant.openAssistant;
    const openConversation = assistant.openConversation;

    useEffect(() => {
        if (isConversationStage || isStandalone) return;
        if (!conversationId) return;
        if (initializedAssistantConversationRef.current !== conversationId) {
            initializedAssistantConversationRef.current = conversationId;
            openAssistant();
        }
        if (openedConversationId !== conversationId) {
            openConversation(conversationId);
        }
    }, [conversationId, isConversationStage, isStandalone, openAssistant, openConversation, openedConversationId]);

    if (!conversationId || !toolCallId) {
        return (
            <main className="flex min-h-full items-center justify-center p-8">
                <div className="surface-panel max-w-xl p-6 text-sm text-[var(--text-secondary)]">
                    Missing widget context.
                </div>
            </main>
        );
    }

    // Only inline-content widgets can be promoted: the endpoint resolves the source
    // fragment by (conversation, tool call) and wraps it for standalone delivery.
    const canSaveAsApp = isContentWidget;
    const handleSaveAsApp = async () => {
        if (!conversationId || !toolCallId || saving) return;
        const name = window.prompt('Save this widget as an app. Name it:', title);
        if (!name || !name.trim()) return;
        setSaving(true);
        setSaveError(null);
        try {
            const app = (await getLemmaClient(podId).apps.createFromWidget({
                conversation_id: conversationId,
                tool_call_id: toolCallId,
                name: name.trim(),
            })) as { url?: string } | undefined;
            setSavedAppUrl(app?.url ?? null);
        } catch (error) {
            setSaveError(error instanceof Error ? error.message : 'Could not save as app.');
        } finally {
            setSaving(false);
        }
    };

    return (
        <main className="presented-resource-surface relative flex h-full min-h-0 flex-col overflow-hidden">
            {canSaveAsApp ? (
                <div className="absolute right-3 top-3 z-10 flex items-center gap-3">
                    {saveError ? (
                        <span className="rounded bg-[var(--bg-canvas)] px-2 py-1 text-xs text-[var(--state-error)] shadow-[var(--shadow-xs)]">{saveError}</span>
                    ) : null}
                    {savedAppUrl ? (
                        <a
                            href={savedAppUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="rounded-full bg-[var(--bg-canvas)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] underline shadow-[var(--shadow-xs)]"
                        >
                            <Check className="mr-1 inline size-3" aria-hidden="true" />
                            Saved — open app
                        </a>
                    ) : (
                        <button
                            type="button"
                            onClick={handleSaveAsApp}
                            disabled={saving}
                            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-3 py-1.5 text-xs font-medium text-[var(--text-primary)] shadow-[var(--shadow-xs)] hover:bg-[var(--bg-subtle)] disabled:opacity-60"
                        >
                            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                            {saving ? 'Saving…' : 'Save as app'}
                        </button>
                    )}
                </div>
            ) : null}
            <div className="h-full min-h-0 flex-1 overflow-hidden">
                <InlineWidget
                    podId={podId}
                    conversationId={conversationId}
                    toolCallId={toolCallId}
                    externalSrc={externalSrc}
                    title={title}
                    loadingMessages={loadingMessages}
                    variant="full"
                />
            </div>
        </main>
    );
}
