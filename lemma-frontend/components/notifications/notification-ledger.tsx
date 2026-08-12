'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { useMarkNotificationRead, type Notification } from '@/lib/hooks/use-notifications';
import {
    buildNotificationDiscussionHref,
    describeNotificationSender,
    flattenNotificationBody,
    getNotificationStateLabel,
    getNotificationTone,
    shortRelativeTime,
} from '@/lib/notifications/notification-display';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import { cn } from '@/lib/utils';

/**
 * Everything already settled, one line each.
 *
 * History is scanned, not read: the question here is *did anyone ever ask me
 * about X*, and the answer is found by running an eye down a column. So one row
 * per ask, no group headers, no buttons — and the state word only where it is
 * news. A green dot next to the word "Answered" on every row of a list of
 * answered things is two channels carrying one bit.
 */
function toneDotClass(notification: Notification) {
    const tone = getNotificationTone(notification);
    if (tone === 'danger') return 'bg-[var(--state-error)]';
    if (tone === 'success') return 'bg-[var(--state-success)]';
    if (tone === 'attention') return 'bg-[var(--action-primary)]';
    return 'bg-[var(--text-tertiary)]';
}

/** Only the states that are not what the whole list is. */
function exceptionalStateLabel(notification: Notification) {
    return notification.status === 'EXPIRED' || notification.status === 'CANCELLED'
        ? getNotificationStateLabel(notification)
        : null;
}

function NotificationLedgerRow({
    notification,
    podId,
    expanded,
    onToggle,
    resolveAgentName,
    deepLinked,
}: {
    notification: Notification;
    podId: string;
    expanded: boolean;
    onToggle: () => void;
    resolveAgentName: (agentId: string) => string | undefined;
    deepLinked: boolean;
}) {
    const markRead = useMarkNotificationRead(podId);
    const readRef = useRef(false);
    const rowRef = useRef<HTMLDivElement>(null);

    // A permalink that lands on the right row and leaves it below the fold has
    // not arrived anywhere. Once, on mount, and only for the named row.
    useEffect(() => {
        if (!deepLinked) return;
        rowRef.current?.scrollIntoView({ block: 'center' });
    }, [deepLinked]);

    useEffect(() => {
        if (!expanded || notification.read_at || readRef.current) return;
        readRef.current = true;
        markRead.mutate(notification.id);
    }, [expanded, markRead, notification.id, notification.read_at]);

    const state = exceptionalStateLabel(notification);
    const trailing = state || describeNotificationSender(notification, resolveAgentName);

    return (
        <div ref={rowRef} className="notification-ledger-item" data-current={deepLinked ? 'true' : undefined}>
            <button
                type="button"
                onClick={onToggle}
                aria-expanded={expanded}
                className="lemma-index-row notification-ledger-row custom-focus-ring"
            >
                <span className={cn('notification-ledger-dot', toneDotClass(notification))} aria-hidden />
                <span className="notification-ledger-title">{notification.title}</span>
                <span className="notification-ledger-preview">
                    {flattenNotificationBody(notification.body)}
                </span>
                <span className="notification-ledger-sender" data-state={state ? 'true' : undefined}>
                    {trailing}
                </span>
                <span className="notification-ledger-time">
                    {shortRelativeTime(notification.created_at)}
                </span>
            </button>

            {expanded ? (
                <div className="notification-ledger-detail">
                    <p className="notification-ledger-ask">{notification.body}</p>
                    {notification.response_summary ? (
                        <p className="notification-ledger-answer">{notification.response_summary}</p>
                    ) : null}
                    <p className="notification-ledger-note">
                        {notification.delivery_platform
                            ? `Sent on ${notification.delivery_platform.toLowerCase()}`
                            : 'Only ever shown here'}
                        {' · '}
                        {formatRelativeTime(notification.created_at)}
                        {notification.responded_at
                            ? ` · answered ${formatRelativeTime(notification.responded_at)}`
                            : null}
                    </p>
                    <div className="notification-ledger-actions">
                        <Button asChild variant="quiet" size="xs">
                            <Link href={buildNotificationDiscussionHref(podId, notification)}>
                                Talk it through
                            </Link>
                        </Button>
                    </div>
                </div>
            ) : null}
        </div>
    );
}

export function NotificationLedger({
    band,
    items,
    podId,
    expandedId,
    onToggle,
    resolveAgentName,
    deepLinkedId,
}: {
    band: string;
    items: Notification[];
    podId: string;
    expandedId: string | null;
    onToggle: (notificationId: string) => void;
    resolveAgentName: (agentId: string) => string | undefined;
    deepLinkedId: string | null;
}) {
    if (items.length === 0) return null;

    return (
        <section className="notification-band">
            <h2 className="notification-band-label">{band}</h2>
            <div className="lemma-index-list">
                {items.map((notification) => (
                    <NotificationLedgerRow
                        key={notification.id}
                        notification={notification}
                        podId={podId}
                        expanded={expandedId === notification.id}
                        onToggle={() => onToggle(notification.id)}
                        resolveAgentName={resolveAgentName}
                        deepLinked={deepLinkedId === notification.id}
                    />
                ))}
            </div>
        </section>
    );
}
