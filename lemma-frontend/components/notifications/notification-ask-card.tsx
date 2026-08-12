'use client';

import { useRef, useState } from 'react';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { ChevronDown } from '@/components/ui/icons';
import {
    useAcknowledgeNotifications,
    useMarkNotificationsRead,
    useRespondToNotifications,
} from '@/lib/hooks/use-notifications';
import {
    buildNotificationDiscussionHref,
    canDismissNotification,
    describeNotificationSender,
    getNotificationActionHref,
    shortRelativeTime,
    type NotificationAskGroup,
} from '@/lib/notifications/notification-display';
import { formatRelativeTime } from '@/lib/utils/relative-time';

/**
 * One open ask, answerable where it sits.
 *
 * The list this replaces put the answer behind a disclosure: click the row, wait
 * for a 4.5rem textarea and three same-weight buttons to unfold, and watch
 * everything below it leave the screen. On a page whose entire job is answering,
 * the answer should not cost a click first — and there are only ever a handful
 * of these, because anything answered has left for the ledger below.
 */
export function NotificationAskCard({
    group,
    podId,
    hoistedReason,
    resolveAgentName,
    resolveFlowName,
}: {
    group: NotificationAskGroup;
    podId: string;
    /** The undeliverable reason already stated once at the top of the page. */
    hoistedReason: string | null;
    resolveAgentName: (agentId: string) => string | undefined;
    resolveFlowName: (flowId: string) => string | undefined;
}) {
    const [draft, setDraft] = useState('');
    const [showRepeats, setShowRepeats] = useState(false);
    const respond = useRespondToNotifications(podId);
    const dismiss = useAcknowledgeNotifications(podId);
    const markRead = useMarkNotificationsRead(podId);
    const readRef = useRef(false);

    const { latest, items } = group;
    const repeats = items.length;
    const sender = describeNotificationSender(latest, resolveAgentName);
    const when = formatRelativeTime(latest.created_at);
    const unread = items.some((item) => !item.read_at);
    const actionHref = getNotificationActionHref(podId, latest, resolveFlowName);
    const discussHref = buildNotificationDiscussionHref(podId, latest);
    const dismissible = canDismissNotification(latest);

    /* Read on first touch, and the whole run at once.
       Marking every card read on mount would fire a mutation per card and
       invalidate the list once per response — a refetch storm to record
       something nobody did yet. Touching the card is a person arriving at it,
       and what they arrived at is all six of these. */
    const noteRead = () => {
        if (readRef.current || !unread) return;
        readRef.current = true;
        markRead.mutate(items.filter((item) => !item.read_at).map((item) => item.id));
    };

    /* Answering answers the whole run. Six identical check-ins are six runs
       waiting on one fact, and closing only the newest leaves five agents still
       waiting for something already said out loud. */
    const submit = () => {
        const summary = draft.trim();
        if (!summary) return;
        respond.mutate(
            { notificationIds: items.map((item) => item.id), summary },
            { onSuccess: () => setDraft('') },
        );
    };

    return (
        <article
            className="notification-card"
            data-unread={unread ? 'true' : undefined}
            onPointerDown={noteRead}
            onFocusCapture={noteRead}
        >
            <div className="notification-card-meta">
                <span className="notification-card-dot" aria-hidden />
                <span className="notification-card-sender">{sender}</span>
                {repeats > 1 ? <span>· asked {repeats} times</span> : null}
                {when ? <span>· {repeats > 1 ? `latest ${when}` : when}</span> : null}
                {repeats > 1 ? (
                    <button
                        type="button"
                        className="notification-card-repeats custom-focus-ring"
                        onClick={() => setShowRepeats((open) => !open)}
                        aria-expanded={showRepeats}
                    >
                        {repeats} asks
                        <ChevronDown
                            className="h-3 w-3"
                            data-open={showRepeats ? 'true' : undefined}
                            aria-hidden
                        />
                    </button>
                ) : null}
            </div>

            <h2 className="notification-card-title">{latest.title}</h2>
            <p className="notification-card-body">{latest.body}</p>

            {/* Every repeat, when the count is doubted. Read-only: they are one
                question, and they close together. */}
            {showRepeats && repeats > 1 ? (
                <ul className="notification-card-repeat-list">
                    {items.map((item) => (
                        <li key={item.id}>
                            {shortRelativeTime(item.created_at)}
                            {item.delivery_platform
                                ? ` · sent on ${item.delivery_platform.toLowerCase()}`
                                : ' · only here'}
                        </li>
                    ))}
                </ul>
            ) : null}

            {/* Left on the card only when it disagrees with the banner — a reason
                every row shares is a fact about the pod, stated once up there. */}
            {latest.delivery_status === 'UNDELIVERABLE' &&
            (latest.undeliverable_reason || null) !== hoistedReason ? (
                <p className="notification-card-note">
                    {latest.undeliverable_reason
                        ? `Not sent to a chat app: ${latest.undeliverable_reason}`
                        : 'Not sent to a chat app.'}
                </p>
            ) : null}

            {/* No capability gate anywhere here: a notification is addressed to
                one person, every endpoint scopes to the caller's own, and a
                read-only member who was asked something is still the person who
                has to answer. */}
            {latest.awaiting_response && !latest.responds_through_action ? (
                <Textarea
                    rows={2}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder="Write your answer"
                    className="notification-card-field resize-none text-sm"
                />
            ) : null}

            {latest.responds_through_action ? (
                <p className="notification-card-note">
                    Answered by filling in the form, so there is nothing to type here.
                </p>
            ) : null}

            <div className="notification-card-actions">
                {latest.awaiting_response && !latest.responds_through_action ? (
                    <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={submit}
                        disabled={!draft.trim() || respond.isPending}
                    >
                        {respond.isPending ? 'Sending…' : 'Send answer'}
                    </Button>
                ) : null}
                {actionHref ? (
                    <Button asChild variant={latest.responds_through_action ? 'secondary' : 'quiet'} size="sm">
                        <Link href={actionHref}>Open the form</Link>
                    </Button>
                ) : null}
                <Button asChild variant="quiet" size="sm">
                    <Link href={discussHref}>Talk it through</Link>
                </Button>
                {repeats > 1 && latest.awaiting_response && !latest.responds_through_action ? (
                    <span className="notification-card-hint">Answers all {repeats}</span>
                ) : null}
                {/* Only where the domain allows it. `acknowledge` is refused for
                    anything that expects a response — you answer it or it
                    expires — so a Dismiss there is a button that can only fail. */}
                {dismissible ? (
                    <Button
                        type="button"
                        variant="quiet"
                        size="sm"
                        className="notification-card-dismiss"
                        onClick={() => dismiss.mutate(items.map((item) => item.id))}
                        disabled={dismiss.isPending}
                    >
                        {repeats > 1 ? `Dismiss all ${repeats}` : 'Dismiss'}
                    </Button>
                ) : null}
            </div>

            {/* Everything failing means the list moved under them — usually
                answered on a phone a minute ago. Say so; failing quietly leaves
                them retyping. */}
            {respond.isError ? (
                <p className="notification-card-error">
                    Could not record that — it may already have been answered elsewhere.
                </p>
            ) : null}
        </article>
    );
}
