'use client';

import { useState } from 'react';
import { Bell } from '@/components/ui/icons';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import {
    useAcknowledgeNotification,
    useMarkAllNotificationsRead,
    useMarkNotificationRead,
    useNotifications,
    useRespondToNotification,
    useUnreadNotificationCount,
    type Notification,
} from '@/lib/hooks/use-notifications';

type NotificationsBellProps = {
    podId: string | undefined;
};

/**
 * A notification row.
 *
 * The action is decided by the server, not guessed here: `awaiting_response`
 * says whether to offer one at all, and `responds_through_action` says whether
 * it is a text reply or a workflow form. A form is answered through the run
 * endpoint, where it is validated against the node's schema — offering a text
 * box for one would give it a second answer path that cannot validate.
 */
function NotificationRow({
    notification,
    podId,
    onOpenAction,
}: {
    notification: Notification;
    podId: string | undefined;
    onOpenAction: (notification: Notification) => void;
}) {
    const [replying, setReplying] = useState(false);
    const [draft, setDraft] = useState('');
    const respond = useRespondToNotification(podId);
    const acknowledge = useAcknowledgeNotification(podId);
    const markRead = useMarkNotificationRead(podId);

    const unread = !notification.read_at;

    const submit = () => {
        const summary = draft.trim();
        if (!summary) return;
        respond.mutate(
            { notificationId: notification.id, summary },
            {
                onSuccess: () => {
                    setReplying(false);
                    setDraft('');
                },
            },
        );
    };

    return (
        <li
            className="border-b border-[var(--border-subtle)] px-3 py-2.5 last:border-b-0"
            data-unread={unread ? 'true' : undefined}
        >
            <div className="flex items-start gap-2">
                {/* An unread dot rather than a bolded row: several bold rows in a
                    short list read as emphasis on the list itself. */}
                <span
                    aria-hidden
                    className={
                        unread
                            ? 'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent-9)]'
                            : 'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-transparent'
                    }
                />
                <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-[var(--text-primary)]">
                        {notification.title}
                    </p>
                    <p className="mt-0.5 whitespace-pre-wrap text-xs leading-5 text-[var(--text-secondary)]">
                        {notification.body}
                    </p>

                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[var(--text-tertiary)]">
                        <span>{formatRelativeTime(notification.created_at)}</span>
                        {notification.delivery_platform ? (
                            <span>· {notification.delivery_platform.toLowerCase()}</span>
                        ) : null}
                        {notification.status === 'RESPONDED' ? (
                            <span>· answered</span>
                        ) : null}
                        {notification.status === 'EXPIRED' ? <span>· expired</span> : null}
                    </div>

                    {/* Undeliverable is not an error state — the notification is
                        right here. But the reason is usually something a person
                        can fix in a minute, so it is worth the line. */}
                    {notification.delivery_status === 'UNDELIVERABLE' &&
                    notification.undeliverable_reason ? (
                        <p className="mt-1 text-[11px] leading-4 text-[var(--text-tertiary)]">
                            Not sent to a chat app: {notification.undeliverable_reason}
                        </p>
                    ) : null}

                    {notification.status === 'RESPONDED' && notification.response_summary ? (
                        <p className="mt-1.5 rounded-md bg-[var(--surface-2)] px-2 py-1 text-xs text-[var(--text-secondary)]">
                            {notification.response_summary}
                        </p>
                    ) : null}

                    {notification.awaiting_response ? (
                        <div className="mt-2">
                            {notification.responds_through_action ? (
                                <button
                                    type="button"
                                    onClick={() => onOpenAction(notification)}
                                    className="custom-focus-ring rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-2 py-1 text-xs text-[var(--text-primary)] transition-colors hover:bg-[var(--surface-3)]"
                                >
                                    Open form
                                </button>
                            ) : replying ? (
                                <div className="flex flex-col gap-1.5">
                                    <textarea
                                        autoFocus
                                        rows={2}
                                        value={draft}
                                        onChange={(event) => setDraft(event.target.value)}
                                        placeholder="Your reply"
                                        className="custom-focus-ring w-full resize-none rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                                    />
                                    <div className="flex items-center gap-1.5">
                                        <button
                                            type="button"
                                            onClick={submit}
                                            disabled={!draft.trim() || respond.isPending}
                                            className="custom-focus-ring rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-2 py-1 text-xs text-[var(--text-primary)] transition-colors hover:bg-[var(--surface-3)] disabled:opacity-50"
                                        >
                                            {respond.isPending ? 'Sending…' : 'Send'}
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setReplying(false)}
                                            className="custom-focus-ring rounded-md px-2 py-1 text-xs text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                                        >
                                            Cancel
                                        </button>
                                    </div>
                                    {/* 409 means somebody answered while this was
                                        open. Say so — the row is stale and
                                        silently failing would leave them retyping. */}
                                    {respond.isError ? (
                                        <p className="text-[11px] text-[var(--text-danger,#c33)]">
                                            Could not record that — it may already have
                                            been answered.
                                        </p>
                                    ) : null}
                                </div>
                            ) : (
                                <button
                                    type="button"
                                    onClick={() => setReplying(true)}
                                    className="custom-focus-ring rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-2 py-1 text-xs text-[var(--text-primary)] transition-colors hover:bg-[var(--surface-3)]"
                                >
                                    Respond
                                </button>
                            )}
                        </div>
                    ) : notification.status === 'OPEN' ? (
                        <button
                            type="button"
                            onClick={() => acknowledge.mutate(notification.id)}
                            className="custom-focus-ring mt-2 rounded-md px-2 py-1 text-xs text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                        >
                            Dismiss
                        </button>
                    ) : null}
                </div>

                {unread ? (
                    <button
                        type="button"
                        onClick={() => markRead.mutate(notification.id)}
                        className="custom-focus-ring shrink-0 rounded-md px-1.5 py-0.5 text-[11px] text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                        title="Mark read"
                    >
                        Mark read
                    </button>
                ) : null}
            </div>
        </li>
    );
}

export function NotificationsBell({ podId }: NotificationsBellProps) {
    const [open, setOpen] = useState(false);
    const { data: unread = 0 } = useUnreadNotificationCount(podId);
    // Only fetch the list once it is actually being looked at. The badge is the
    // always-on part and it is one integer.
    const { data, isLoading } = useNotifications(open ? podId : undefined);
    const markAllRead = useMarkAllNotificationsRead(podId);

    const items = data?.items ?? [];

    const openAction = (notification: Notification) => {
        const action = (notification.action ?? {}) as { run_id?: string };
        if (action.run_id && podId) {
            window.location.href = `/pod/${podId}/flows/runs/${action.run_id}`;
        }
    };

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <button
                    type="button"
                    className="lemma-shell-icon-button custom-focus-ring relative h-8 w-8 shrink-0 self-center text-[var(--text-tertiary)] data-[state=open]:text-[var(--text-primary)]"
                    aria-label={
                        unread > 0
                            ? `Notifications, ${unread} unread`
                            : 'Notifications'
                    }
                    title="Notifications"
                >
                    <Bell className="h-4 w-4" strokeWidth={1.8} />
                    {unread > 0 ? (
                        <span
                            aria-hidden
                            className="absolute right-1 top-1 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-[var(--accent-9)] px-1 text-[9px] font-medium leading-none text-[var(--accent-contrast,#fff)]"
                        >
                            {unread > 9 ? '9+' : unread}
                        </span>
                    ) : null}
                </button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-[22rem] p-0">
                <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-3 py-2">
                    <span className="text-sm font-medium text-[var(--text-primary)]">
                        Notifications
                    </span>
                    {unread > 0 ? (
                        <button
                            type="button"
                            onClick={() => markAllRead.mutate()}
                            className="custom-focus-ring rounded-md px-1.5 py-0.5 text-xs text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                        >
                            Mark all read
                        </button>
                    ) : null}
                </div>

                {isLoading ? (
                    <p className="px-3 py-6 text-center text-xs text-[var(--text-tertiary)]">
                        Loading…
                    </p>
                ) : items.length === 0 ? (
                    <p className="px-3 py-6 text-center text-xs text-[var(--text-tertiary)]">
                        Nothing needs you right now.
                    </p>
                ) : (
                    <ul className="max-h-[26rem] overflow-y-auto">
                        {items.map((notification) => (
                            <NotificationRow
                                key={notification.id}
                                notification={notification}
                                podId={podId}
                                onOpenAction={openAction}
                            />
                        ))}
                    </ul>
                )}
            </PopoverContent>
        </Popover>
    );
}
