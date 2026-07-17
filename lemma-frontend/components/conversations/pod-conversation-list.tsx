'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { formatDistanceToNow } from 'date-fns';
import { ArrowRight, Loader2, Plus, Sparkles } from '@/components/ui/icons';
import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { ResourceList, ResourceMetric, ResourceMetricStrip, ResourceRow } from '@/components/pod/resource-layout';
import { InlineEmptyState } from '@/components/shared/empty-state';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { getConversationStatusView, isConversationRunningStatus } from '@/lib/utils/conversations';

interface PodConversationListProps {
    podId: string;
    podName?: string;
    variant?: 'compact' | 'page';
    limit?: number;
    scopeType?: 'pod' | 'assistant';
    scopeName?: string;
    showHeader?: boolean;
}

export function PodConversationList({
    podId,
    podName,
    variant = 'compact',
    limit = variant === 'compact' ? 6 : 100,
    scopeType = 'pod',
    scopeName,
    showHeader = variant === 'page',
}: PodConversationListProps) {
    const {
        conversations,
        openedConversationId,
        isLoadingConversations,
    } = useAIAssistant();
    const router = useRouter();

    const isCompact = variant === 'compact';
    const items = conversations.slice(0, limit);
    const conversationCount = conversations.length;
    const entityName = scopeName || podName;
    const isAssistantScope = scopeType === 'assistant';
    const runningCount = conversations.filter((conversation) => isConversationRunningStatus(conversation.status)).length;
    const recentCount = conversations.filter((conversation) => {
        const updatedAt = new Date(conversation.updated_at || conversation.created_at).getTime();
        return Number.isFinite(updatedAt) && Date.now() - updatedAt < 1000 * 60 * 60 * 24 * 7;
    }).length;

    const openConversation = (conversationId: string) => {
        router.push(`/pod/${podId}/conversations/${encodeURIComponent(conversationId)}`);
    };

    const startNewConversation = () => {
        router.push(`/pod/${podId}/conversations/new`);
    };

    const listBody = (
        <ResourceList className={isCompact ? 'gap-px' : 'gap-1'}>
            {isLoadingConversations && items.length === 0 && (
                <div className="flex items-center gap-2 px-2 py-5 text-sm text-[var(--text-tertiary)]">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Loading conversations
                </div>
            )}

            {!isLoadingConversations && items.length === 0 && (
                <InlineEmptyState
                    icon={<Sparkles className="h-4 w-4" />}
                    title="No conversations yet"
                    description={isAssistantScope
                        ? 'Start a conversation with this assistant and continue it here later.'
                        : 'Start a Lemma Assistant conversation and continue it here later.'}
                    action={(
                        <Button size="sm" onClick={startNewConversation} className="shrink-0 gap-1.5">
                            <Plus className="h-3.5 w-3.5" />
                            New
                        </Button>
                    )}
                    className="px-2 py-5"
                />
            )}

            {items.map((conversation) => {
                const statusView = getConversationStatusView(conversation.status);
                const showStatus = statusView.state !== 'completed' && statusView.state !== 'unknown';

                return (
                    <ResourceRow
                        key={conversation.id}
                        className={cn(
                            'group px-1 py-1',
                            openedConversationId === conversation.id && 'bg-[color:color-mix(in_srgb,var(--surface-2)_68%,transparent)]'
                        )}
                    >
                        <button
                            type="button"
                            onClick={() => openConversation(conversation.id)}
                            className="conversation-list-row-button flex min-h-12 w-full min-w-0 items-center gap-3 rounded-md px-1.5 text-left outline-none transition-colors focus:outline-none focus-visible:outline-none"
                        >
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-sm font-normal text-[var(--text-primary)]">
                                    {conversation.title || 'Untitled conversation'}
                                </span>
                                <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[var(--text-tertiary)]">
                                    <span>{formatDistanceToNow(new Date(conversation.updated_at || conversation.created_at), { addSuffix: true })}</span>
                                    {showStatus ? (
                                        <span
                                            className={cn(
                                                statusView.tone === 'live' && 'text-[var(--delight)]',
                                                statusView.tone === 'warning' && 'text-[var(--state-warning)]',
                                                statusView.tone === 'danger' && 'text-[var(--state-error)]'
                                            )}
                                        >
                                            {statusView.label}
                                        </span>
                                    ) : null}
                                    {isAssistantScope && entityName ? <span className="truncate">{entityName}</span> : null}
                                </span>
                            </span>
                            <span className="shrink-0 text-xs text-[var(--text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100">
                                Open
                            </span>
                        </button>
                    </ResourceRow>
                );
            })}
        </ResourceList>
    );

    if (isCompact) {
        return (
            <div className="rounded-lg border border-[color:color-mix(in_srgb,var(--border-subtle)_48%,transparent)] bg-transparent p-3">
                <div className="mb-2 flex items-center justify-between gap-3 px-1">
                    <div>
                        <p className="text-sm font-medium text-[var(--text-primary)]">Recent conversations</p>
                        <p className="text-xs text-[var(--text-tertiary)]">
                            {entityName
                                ? `${entityName} chats.`
                                : isAssistantScope
                                    ? 'Assistant chat history.'
                                    : 'Lemma Assistant chat history.'}
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-[var(--text-tertiary)]">
                            {conversationCount}
                        </span>
                        <Link
                            href={`/pod/${podId}/conversations`}
                            className="inline-flex items-center gap-1 text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        >
                            View all <ArrowRight className="h-3.5 w-3.5" />
                        </Link>
                    </div>
                </div>
                {listBody}
            </div>
        );
    }

    return (
        <div className="space-y-4">
            {showHeader ? (
                <div className="mb-5 flex items-center justify-between gap-3">
                    <div>
                        <h1 className="font-display text-3xl font-normal text-[var(--text-primary)]">
                            {isAssistantScope ? 'Assistant Conversations' : 'Pod Conversations'}
                        </h1>
                        <p className="mt-1 text-sm text-[var(--text-secondary)]">
                            {isAssistantScope
                                ? 'Reopen and continue conversations for this assistant.'
                                : 'Reopen and continue assistant threads for this pod.'}
                        </p>
                    </div>
                    <Button onClick={startNewConversation} className="gap-2">
                        <Plus className="h-4 w-4" />
                        New conversation
                    </Button>
                </div>
            ) : null}

            <ResourceMetricStrip>
                <ResourceMetric value={conversationCount} label="conversations" active />
                <ResourceMetric value={runningCount} label="running" />
                <ResourceMetric value={recentCount} label="recent" />
            </ResourceMetricStrip>
            {listBody}
        </div>
    );
}
