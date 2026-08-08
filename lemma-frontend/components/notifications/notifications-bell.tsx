'use client';

import { useState } from 'react';
import { Bell } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
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

                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-4 text-[var(--text-tertiary)]">
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
                        <p className="mt-1 text-xs leading-4 text-[var(--text-tertiary)]">
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
                                <Button
                                    type="button"
                                    variant="secondary"
                                    size="xs"
                                    onClick={() => onOpenAction(notification)}
                                >
                                    Open form
                                </Button>
                            ) : replying ? (
                                <div className="flex flex-col gap-1.5">
                                    <Textarea
                                        autoFocus
                                        rows={2}
                                        value={draft}
                                        onChange={(event) => setDraft(event.target.value)}
                                        placeholder="Your reply"
                                        className="min-h-16 resize-none text-xs"
                                    />
                                    <div className="flex items-center gap-1.5">
                                        <Button
                                            type="button"
                                            variant="secondary"
                                            size="xs"
                                            onClick={submit}
                                            disabled={!draft.trim() || respond.isPending}
                                        >
                                            {respond.isPending ? 'Sending…' : 'Send'}
                                        </Button>
                                        <Button
                                            type="button"
                                            variant="quiet"
                                            size="xs"
                                            onClick={() => setReplying(false)}
                                        >
                                            Cancel
                                        </Button>
                                    </div>
                                    {/* 409 means somebody answered while this was
                                        open. Say so — the row is stale and
                                        silently failing would leave them retyping. */}
                                    {respond.isError ? (
                                        <p className="text-xs leading-4 text-[var(--state-error)]">
                                            Could not record that — it may already have
                                            been answered.
                                        </p>
                                    ) : null}
                                </div>
                            ) : (
                                <Button
                                    type="button"
                                    variant="secondary"
                                    size="xs"
                                    onClick={() => setReplying(true)}
                                >
                                    Respond
                                </Button>
                            )}
                        </div>
                    ) : notification.status === 'OPEN' ? (
                        <Button
                            type="button"
                            variant="quiet"
                            size="xs"
                            className="mt-2"
                            onClick={() => acknowledge.mutate(notification.id)}
                        >
                            Dismiss
                        </Button>
                    ) : null}
                </div>

                {unread ? (
                    <Button
                        type="button"
                        variant="quiet"
                        size="xs"
                        className="shrink-0"
                        onClick={() => markRead.mutate(notification.id)}
                        title="Mark read"
                    >
                        Mark read
                    </Button>
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
                            className="absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--accent-9)] px-1 text-xs font-medium leading-none text-[var(--text-on-brand)]"
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
                        <Button
                            type="button"
                            variant="quiet"
                            size="xs"
                            onClick={() => markAllRead.mutate()}
                        >
                            Mark all read
                        </Button>
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
