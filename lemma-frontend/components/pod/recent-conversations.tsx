'use client';

import Link from 'next/link';
import { ChevronRight, MessageCircle } from '@/components/ui/icons';

import { StartConversationButton } from '@/components/pod/start-conversation-button';
import { buildScopedConversationHref } from '@/lib/assistant/conversation-composer-context';
import { requestConversationStageNavigation } from '@/lib/assistant/conversation-presentation';
import { formatRelativeTime } from '@/lib/utils/relative-time';

// Shared between the agent detail page and the pod assistant page — the same
// list, scoped to a named agent or to the pod default.

// The clock moved to `lib/utils/relative-time` once the home pod list needed it
// too. Re-exported here so the surfaces that already import it from this module
// keep working.
export { formatRelativeTime };

export function RecentConversations({
    podId,
    conversations,
    agentName,
}: {
    podId: string;
    conversations: Array<{ id: string; title?: string | null; updated_at?: string; created_at?: string }>;
    /** Agent to start a new conversation with, or `null` for the pod default assistant. */
    agentName: string | null;
}) {
    if (conversations.length === 0) return null;

    return (
        <section className="mt-9">
            <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-base font-normal leading-snug text-[var(--text-secondary)]">Recent conversations</h2>
                <StartConversationButton podId={podId} agentName={agentName} label="New" variant="quiet" />
            </div>
            <div className="lemma-index-list">
                {conversations.map((conversation) => {
                    const timestamp = formatRelativeTime(conversation.updated_at ?? conversation.created_at);
                    const href = buildScopedConversationHref({
                        podId,
                        conversationId: conversation.id,
                        agentName,
                    });
                    return (
                        <Link
                            key={conversation.id}
                            href={href}
                            onClick={(event) => {
                                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                                if (requestConversationStageNavigation(href)) event.preventDefault();
                            }}
                            className="lemma-index-row group flex items-center gap-2.5"
                        >
                            <MessageCircle className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden />
                            <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]">
                                {conversation.title?.trim() || 'Untitled conversation'}
                            </span>
                            {timestamp ? (
                                <span className="hidden shrink-0 text-xs text-[var(--text-tertiary)] sm:inline">{timestamp}</span>
                            ) : null}
                            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)] opacity-0 transition-[opacity,transform] group-hover:translate-x-0.5 group-hover:opacity-100" aria-hidden />
                        </Link>
                    );
                })}
            </div>
        </section>
    );
}
