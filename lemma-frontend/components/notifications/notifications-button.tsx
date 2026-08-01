'use client';

import { Bell } from '@phosphor-icons/react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
    useMarkAllNotificationsRead,
    useMarkNotificationRead,
    useNotifications,
    useUnreadNotificationCount,
} from '@/lib/hooks/use-notifications';
import { cn } from '@/lib/utils';

/**
 * The inbox.
 *
 * Agents can now start conversations, which means there is finally something to
 * be notified *about* — and the app is the one channel that cannot fail, expire,
 * or be muted, so this is where every proactive message lands regardless of
 * where else it went.
 *
 * Opening an item goes to the conversation it belongs to rather than expanding
 * in place: these are the openings of conversations, not alerts, and the useful
 * next action is almost always to reply.
 */
export function NotificationsButton({ podId }: { podId?: string }) {
    const router = useRouter();
    const [open, setOpen] = useState(false);
    const { data: countData } = useUnreadNotificationCount(podId);
    // Only fetch the list once someone actually looks.
    const { data, isLoading } = useNotifications({ podId, enabled: open });
    const markRead = useMarkNotificationRead();
    const markAllRead = useMarkAllNotificationsRead(podId);

    const unread = countData?.unread_count ?? 0;
    const items = data?.items ?? [];

    const openItem = (item: { id: string; conversation_id?: string | null; read_at?: string | null }) => {
        if (!item.read_at) markRead.mutate(item.id);
        setOpen(false);
        if (item.conversation_id && podId) {
            router.push(`/pod/${podId}/conversations/${item.conversation_id}`);
        }
    };

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <button
                    type="button"
                    aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
                    title="Notifications"
                    className="workspace-sidebar-trigger-button custom-focus-ring relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--text-primary)] transition-colors hover:bg-[var(--surface-2)]"
                >
                    <Bell className="h-4 w-4" aria-hidden="true" />
                    {unread > 0 ? (
                        <span
                            aria-hidden="true"
                            className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--accent-solid,#2563eb)] px-1 text-[10px] font-medium leading-none text-white"
                        >
                            {unread > 9 ? '9+' : unread}
                        </span>
                    ) : null}
                </button>
            </PopoverTrigger>
            <PopoverContent align="start" side="top" className="w-80 p-0">
                <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-3 py-2">
                    <span className="type-eyebrow-medium">Notifications</span>
                    {unread > 0 ? (
                        <button
                            type="button"
                            onClick={() => markAllRead.mutate()}
                            className="custom-focus-ring rounded text-xs text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                        >
                            Mark all read
                        </button>
                    ) : null}
                </div>

                <div className="max-h-80 overflow-y-auto">
                    {isLoading ? (
                        <div className="space-y-2 p-3" role="status" aria-label="Loading notifications">
                            {['w-3/5', 'w-4/5', 'w-1/2'].map((width) => (
                                <div
                                    key={width}
                                    className={cn('h-3 rounded bg-[var(--surface-2)]', width)}
                                />
                            ))}
                        </div>
                    ) : items.length === 0 ? (
                        <p className="px-3 py-6 text-center text-sm text-[var(--text-tertiary)]">
                            Nothing yet. Your agents will tell you here.
                        </p>
                    ) : (
                        items.map((item) => (
                            <button
                                key={item.id}
                                type="button"
                                onClick={() => openItem(item)}
                                className="lemma-sidebar-row custom-focus-ring w-full items-start gap-2 px-3 py-2 text-left"
                            >
                                <span className="flex w-3.5 shrink-0 items-center justify-center pt-1.5" aria-hidden="true">
                                    <span
                                        className={cn(
                                            'block h-1.5 w-1.5 rounded-full',
                                            item.read_at
                                                ? 'border border-current opacity-45'
                                                : 'bg-[var(--accent-solid,#2563eb)]',
                                        )}
                                    />
                                </span>
                                <span className="min-w-0 flex-1">
                                    {item.title ? (
                                        <span className="block truncate text-sm font-medium text-[var(--text-primary)]">
                                            {item.title}
                                        </span>
                                    ) : null}
                                    <span
                                        className={cn(
                                            'block text-sm text-[var(--text-secondary)]',
                                            item.title ? 'line-clamp-2' : 'line-clamp-3',
                                        )}
                                    >
                                        {item.body}
                                    </span>
                                </span>
                            </button>
                        ))
                    )}
                </div>
            </PopoverContent>
        </Popover>
    );
}
