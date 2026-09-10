'use client';

import { use, useState } from 'react';
import { useRouter } from 'next/navigation';
import { PanelLeftOpen, Plus } from '@/components/ui/icons';
import { PodConversationList } from '@/components/conversations/pod-conversation-list';
import { usePodLayout } from '@/components/pod/pod-layout-context';
import { Button } from '@/components/ui/button';
import { usePod } from '@/lib/hooks/use-pods';

export default function PodConversationsPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const router = useRouter();
    const { data: pod } = usePod(podId);
    const { isCompact, toggleNav } = usePodLayout();
    const [showArchived, setShowArchived] = useState(false);

    const startNewConversation = () => {
        router.push(`/pod/${podId}/conversations/new`);
    };

    // `shrink-0` is what keeps the header below sticky. This div is a flex item
    // of a surface exactly the viewport tall, and `min-h-full` replaces the
    // content-based `min-height: auto` a flex item would otherwise get -- so
    // without it the box caps at the surface height, and the header, which can
    // only stick inside its own containing block, scrolls away with the list.
    return (
        <div className="flex min-h-full shrink-0 flex-col bg-[var(--pod-main-bg)]">
            <header className="pod-shell-topbar sticky top-0 z-10 flex h-14 shrink-0 items-center px-4 sm:px-6 lg:px-8">
                <div className="flex h-8 w-full items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
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
                        <h1 className="min-w-0 truncate text-sm font-medium leading-none text-[var(--text-primary)]">
                            Conversations
                        </h1>
                        {/* Two lists, one control -- the archive is a place you
                            switch to, not a filter you tick. */}
                        <div className="segmented-control ml-2" role="group" aria-label="Conversation list">
                            <button
                                type="button"
                                onClick={() => setShowArchived(false)}
                                className="segmented-control-item"
                                data-active={!showArchived}
                                aria-pressed={!showArchived}
                            >
                                Active
                            </button>
                            <button
                                type="button"
                                onClick={() => setShowArchived(true)}
                                className="segmented-control-item"
                                data-active={showArchived}
                                aria-pressed={showArchived}
                            >
                                Archived
                            </button>
                        </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                        <Button variant="secondary" type="button" size="sm" className="gap-2" onClick={startNewConversation}>
                            <Plus className="h-4 w-4" />
                            New conversation
                        </Button>
                    </div>
                </div>
            </header>
            <div className="px-4 pb-8 pt-5 sm:px-6 lg:px-8">
                <PodConversationList
                    podId={podId}
                    podName={pod?.name}
                    variant="page"
                    showHeader={false}
                    archived={showArchived}
                />
            </div>
        </div>
    );
}
