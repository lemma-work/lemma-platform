'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { ChevronDown } from '@/components/ui/icons';
import { RunInputForm } from '@/components/flows/run-detail/run-input-form';
import {
    useAcknowledgeNotifications,
    useMarkNotificationsRead,
    useRespondToNotifications,
    useSubmitNotificationForm,
} from '@/lib/hooks/use-notifications';
import {
    buildNotificationDiscussionHref,
    canDismissNotification,
    describeNotificationSender,
    getNotificationActionHref,
    getNotificationFormAction,
    isUndelivered,
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
    canSubmitForms,
    highlighted,
    resolveAgentName,
    resolveFlowName,
}: {
    group: NotificationAskGroup;
    podId: string;
    /** The undeliverable reason already stated once at the top of the page. */
    hoistedReason: string | null;
    /** `workflow.execute` — what the run endpoint asks of a form answer. */
    canSubmitForms: boolean;
    /** This is the ask a link asked for. Scroll to it and mark it. */
    highlighted: boolean;
    resolveAgentName: (agentId: string) => string | undefined;
    resolveFlowName: (flowId: string) => string | undefined;
}) {
    const [draft, setDraft] = useState('');
    const [showRepeats, setShowRepeats] = useState(false);
    const respond = useRespondToNotifications(podId);
    const dismiss = useAcknowledgeNotifications(podId);
    const markRead = useMarkNotificationsRead(podId);
    const submitForm = useSubmitNotificationForm(podId);
    const readRef = useRef(false);
    const cardRef = useRef<HTMLElement>(null);

    const { latest, items } = group;
    const repeats = items.length;
    const sender = describeNotificationSender(latest, resolveAgentName);
    const when = formatRelativeTime(latest.created_at);
    const unread = items.some((item) => !item.read_at);
    const formAction = getNotificationFormAction(latest);
    const actionHref = getNotificationActionHref(podId, latest, resolveFlowName);
    const discussHref = buildNotificationDiscussionHref(podId, latest);
    const dismissible = canDismissNotification(latest);
    const answerable = latest.awaiting_response && !latest.responds_through_action;
    // The form is drawn here when the schema is on the payload and the person
    // may submit it. Neither is guaranteed, so the card has to have something to
    // say when it cannot draw one.
    const showForm = latest.awaiting_response && !!formAction && canSubmitForms;

    // A link that lands on the right card and leaves it below the fold has not
    // arrived anywhere. Once, on mount, and only for the card that was asked for.
    useEffect(() => {
        if (!highlighted) return;
        cardRef.current?.scrollIntoView({ block: 'center' });
    }, [highlighted]);

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
            ref={cardRef}
            className="notification-card"
            data-unread={unread ? 'true' : undefined}
            data-current={highlighted ? 'true' : undefined}
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

            {/* Left on the card only when the banner is not already carrying this
                exact reason — a reason every row shares is a fact about the pod,
                stated once up there. The comparison is against a *drawn* banner:
                when `hoistedReason` is null nothing was hoisted, so the note has
                to appear here even for a failure that came with no reason at all,
                which is how an undelivered ask used to show nothing anywhere. */}
            {isUndelivered(latest) &&
            (hoistedReason === null || latest.undeliverable_reason !== hoistedReason) ? (
                <p className="notification-card-note">
                    {latest.undeliverable_reason
                        ? `Not delivered: ${latest.undeliverable_reason}`
                        : 'Not delivered anywhere — it is only here.'}
                </p>
            ) : null}

            {/* No capability gate on a text answer: a notification is addressed
                to one person, every endpoint scopes to the caller's own, and a
                read-only member who was asked something is still the person who
                has to answer. A form is the exception, and only because the run
                endpoint says so — see below. */}
            {answerable ? (
                <Textarea
                    rows={2}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder="Write your answer"
                    className="notification-card-field resize-none text-sm"
                />
            ) : null}

            {/* The form itself, on the card, from the resolved schema the
                executor already put on the action payload. Sending somebody to
                the run page to answer was the one ask on this page that could
                not be answered where it sits — and when they could not read
                workflows the link was not built at all, leaving a card that said
                "fill in the form" and offered no form. */}
            {showForm && formAction ? (
                <div className="notification-card-form">
                    <RunInputForm
                        nodeId={formAction.nodeId}
                        // No nodes to hand it, and none needed: `nodes` exists
                        // only to fall back to the node's *template* schema, and
                        // the resolved one is right here.
                        nodes={[]}
                        schema={formAction.schema}
                        variant="flat"
                        heading={false}
                        onSubmitInput={async (nodeId, inputs) => {
                            await submitForm.mutateAsync({
                                runId: formAction.runId,
                                nodeId,
                                inputs,
                            });
                        }}
                    />
                </div>
            ) : null}

            {/* Why there is no form, when there is no form. Both cases are real:
                a payload written before the schema rode along, and a member who
                may be asked but may not resume a run. */}
            {latest.awaiting_response && !showForm && latest.responds_through_action ? (
                <p className="notification-card-note">
                    {formAction
                        ? 'Answering this needs permission to run workflows, which you do not have — ask somebody who does, or talk it through below.'
                        : 'This one is answered by a workflow form, which has to be opened on its run.'}
                </p>
            ) : null}

            <div className="notification-card-actions">
                {answerable ? (
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
                    <Button
                        asChild
                        variant={latest.responds_through_action && !showForm ? 'secondary' : 'quiet'}
                        size="sm"
                    >
                        <Link href={actionHref}>{showForm ? 'Open the run' : 'Open the form'}</Link>
                    </Button>
                ) : null}
                <Button asChild variant="quiet" size="sm">
                    <Link href={discussHref}>Talk it through</Link>
                </Button>
                {repeats > 1 && answerable ? (
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

            {/* Some of them failing is not the same as none, and it used to read
                as success. The card keeps its identity while the group shrinks
                under it, so this survives long enough to be read. */}
            {respond.data && respond.data.failed > 0 ? (
                <p className="notification-card-note">
                    Answered {respond.data.settled} of {respond.data.settled + respond.data.failed} —
                    the rest had already been closed.
                </p>
            ) : null}
        </article>
    );
}
