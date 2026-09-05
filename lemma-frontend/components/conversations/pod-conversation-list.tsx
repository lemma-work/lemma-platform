'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { formatDistanceToNow } from 'date-fns';
import { Archive, ArrowRight, Plus, Sparkles } from '@/components/ui/icons';
import { toast } from 'sonner';
import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { useScopedConversations, useUpdateConversation } from '@/lib/hooks/use-assistants';
import { ResourceList, ResourceMetric, ResourceMetricStrip, ResourceRow } from '@/components/pod/resource-layout';
import { EmptyState } from '@/components/shared/empty-state';
import { Skeleton } from '@/components/shared/loading';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { getConversationStatusView, isConversationRunningStatus } from '@/lib/utils/conversations';
import { readSource, sourceHeadline } from '@/lib/assistant/conversation-source';
import { SourceGlyph } from '@/components/lemma/assistant/conversation-source-marks';
import { DEFAULT_RESPONDER_NAME } from '@/lib/utils/agents';

/** Titles vary, so the placeholders do — equal bars read as a table, not a list.
 * Widths double as the row count: five lines, matching the settled row height. */
const CONVERSATION_SKELETON_WIDTHS = ['w-3/5', 'w-5/12', 'w-2/3', 'w-1/2', 'w-7/12'];

interface PodConversationListProps {
    podId: string;
    podName?: string;
    variant?: 'compact' | 'page';
    limit?: number;
    scopeType?: 'pod' | 'assistant';
    scopeName?: string;
    showHeader?: boolean;
    /**
     * Show what has been put away instead of the history. A separate read
     * rather than a filter over the same one: the assistant context holds the
     * live conversations for this pod and archived ones are, by definition,
     * not among them.
     */
    archived?: boolean;
}

export function PodConversationList({
    podId,
    podName,
    variant = 'compact',
    limit = variant === 'compact' ? 6 : 100,
    scopeType = 'pod',
    scopeName,
    showHeader = variant === 'page',
    archived = false,
}: PodConversationListProps) {
    const {
        conversations,
        openedConversationId,
        isLoadingConversations,
    } = useAIAssistant();
    const router = useRouter();
    const updateConversation = useUpdateConversation();

    const archive = useScopedConversations(
        { podId },
        { archived: true, limit, enabled: archived },
    );

    const restore = (conversationId: string) => {
        updateConversation.mutate(
            { podId, conversationId, data: { is_archived: false } },
            {
                onSuccess: () => toast.success('Conversation restored'),
                onError: (error) => toast.error(`Could not restore: ${error.message}`),
            },
        );
    };

    const isCompact = variant === 'compact';
    const source = archived ? archive.data?.items ?? [] : conversations;
    const items = source.slice(0, limit);
    const conversationCount = source.length;
    const entityName = scopeName || podName;
    const isAssistantScope = scopeType === 'assistant';
    const runningCount = source.filter((conversation) => isConversationRunningStatus(conversation.status)).length;
    const recentCount = source.filter((conversation) => {
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
            {/* One placeholder line per row, at the row's own height. A centred
                spinner-and-caption was a third box of a third size between the
                empty state and the list, so this sidebar changed shape twice on
                every load. */}
            {(archived ? archive.isLoading : isLoadingConversations) && items.length === 0 && (
                <div role="status" aria-label="Loading conversations">
                    {CONVERSATION_SKELETON_WIDTHS.map((width, index) => (
                        <div key={index} className="px-1 py-1">
                            <div className="flex min-h-12 flex-col justify-center gap-1.5 px-1.5">
                                <Skeleton className={cn('h-3', width)} />
                                <Skeleton className="h-2.5 w-20" />
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {archived && !archive.isLoading && items.length === 0 && (
                <EmptyState variant="inline"
                    icon={<Archive className="h-4 w-4" />}
                    title="Nothing archived"
                    description="Conversations you archive are kept here, and come back the moment one gets a new message."
                    className="px-2 py-5"
                />
            )}

            {!archived && !isLoadingConversations && items.length === 0 && (
                <EmptyState variant="inline"
                    icon={<Sparkles className="h-4 w-4" />}
                    title="No conversations yet"
                    description={isAssistantScope
                        ? 'Start a conversation with this agent and continue it here later.'
                        : `Start a conversation with ${DEFAULT_RESPONDER_NAME} and continue it here later.`}
                    action={(
                        <Button variant="secondary" size="sm" onClick={startNewConversation} className="shrink-0 gap-1.5">
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
                // Null for everything typed here. This row already has a
                // metadata line, so the source can be a word on it rather than
                // a glyph competing with the title.
                const source = readSource(conversation);

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
                                    {source ? (
                                        <span className="flex min-w-0 items-center gap-1.5">
                                            <SourceGlyph source={source} />
                                            <span className="truncate">{sourceHeadline(source)}</span>
                                        </span>
                                    ) : null}
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
                        {archived ? (
                            <Button
                                variant="quiet"
                                size="sm"
                                className="mr-1 shrink-0"
                                disabled={updateConversation.isPending}
                                onClick={() => restore(conversation.id)}
                            >
                                Restore
                            </Button>
                        ) : null}
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
                                    ? "This agent's chat history."
                                    : "This pod's chat history."}
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
                            {isAssistantScope ? `${entityName} Conversations` : 'Pod Conversations'}
                        </h1>
                        <p className="mt-1 text-sm text-[var(--text-secondary)]">
                            {isAssistantScope
                                ? "Reopen and continue this agent's conversations."
                                : "Reopen and continue this pod's conversations."}
                        </p>
                    </div>
                    <Button variant="primary" onClick={startNewConversation} className="gap-2">
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
