'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';

import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Skeleton } from '@/components/shared/loading';
import { AlertTriangle } from '@/components/ui/icons';
import { NotificationAskCard } from '@/components/notifications/notification-ask-card';
import { NotificationLedger } from '@/components/notifications/notification-ledger';
import { useAgents } from '@/lib/hooks/use-agents';
import { useFlows } from '@/lib/hooks/use-flows';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import {
    CLOSED_NOTIFICATION_STATUSES,
    UNATTENDED_NOTIFICATION_STATUSES,
    useInfiniteNotifications,
    useMarkAllNotificationsRead,
    useUnreadNotificationCount,
} from '@/lib/hooks/use-notifications';
import {
    groupIdenticalAsks,
    isFromToday,
    sharedUndeliverableReason,
} from '@/lib/notifications/notification-display';

type NotificationsScope = 'open' | 'all';

const SCOPE_TABS: Array<{ value: NotificationsScope; label: string }> = [
    { value: 'open', label: 'Needs you' },
    { value: 'all', label: 'Everything' },
];

function NotificationsSkeleton() {
    return (
        <div role="status" aria-label="Loading notifications">
            {[0, 1].map((card) => (
                <div key={card} className="notification-card">
                    <Skeleton className="h-3 w-40" />
                    <Skeleton className="mt-3 h-3.5 w-56" />
                    <Skeleton className="mt-2.5 h-3 w-full max-w-lg" />
                    <Skeleton className="mt-4 h-16 w-full max-w-[34rem]" />
                </div>
            ))}
        </div>
    );
}

/**
 * The inbox: what still wants an answer, and everything that no longer does.
 *
 * Two shapes, because they are two different jobs. An open ask is a task — read
 * it whole, answer it here, one card each, and there are only ever a few. A
 * settled one is a record — one line, scanned, opened only when someone is
 * looking for something specific.
 *
 * What this replaced grouped by `origin_conversation_id`, which every scheduled
 * run makes unique: six check-ins produced six "threads" of one, each paying for
 * a heading and a rule, with the subject repeated on all six anyway. Repeats are
 * now collapsed on what actually makes two asks the same ask, and history is
 * ordered by the clock, which is the only order a reader can trust.
 */
export function NotificationsView({ podId }: { podId: string }) {
    const searchParams = useSearchParams();
    const podAccess = usePodAccess(podId);

    // A link from elsewhere can name the row it wants. Read once as initial
    // state rather than reconciled in an effect, which would render the wrong
    // list first and then set state from a render. A named row opens on
    // "Everything": the scope that contains every notification, so a permalink
    // to an answered one never lands on an empty list.
    const deepLinkedId = searchParams.get('n');
    const [scope, setScope] = useState<NotificationsScope>(deepLinkedId ? 'all' : 'open');
    const [expandedId, setExpandedId] = useState<string | null>(deepLinkedId);

    const { data: unread = 0 } = useUnreadNotificationCount(podId);
    const markAllRead = useMarkAllNotificationsRead(podId);

    const openQuery = useInfiniteNotifications(podId, {
        status: UNATTENDED_NOTIFICATION_STATUSES,
    });
    // Only under "Everything", and asked for by status rather than filtered
    // here — the two zones partition the inbox exactly, so paging a mixed list
    // would count rows this half never draws.
    const historyQuery = useInfiniteNotifications(scope === 'all' ? podId : undefined, {
        status: CLOSED_NOTIFICATION_STATUSES,
    });

    // Only to name who asked, and to turn a form's `flow_id` into the name its
    // run route is keyed on. Both cheap, cached, shared with the rest of the pod.
    const { data: agentList } = useAgents(podAccess.can('agent.read') ? podId : undefined);
    const { data: flows = [] } = useFlows(podAccess.can('workflow.read') ? podId : undefined);

    const resolveAgentName = useMemo(() => {
        const names = new Map<string, string>();
        (agentList?.items || []).forEach((agent) => {
            if (agent.id) names.set(agent.id, agent.name);
        });
        return (agentId: string) => names.get(agentId);
    }, [agentList?.items]);

    const resolveFlowName = useMemo(() => {
        const names = new Map<string, string>();
        flows.forEach((flow) => {
            if (flow.id) names.set(flow.id, flow.name);
        });
        return (flowId: string) => names.get(flowId);
    }, [flows]);

    const openItems = useMemo(
        () => (openQuery.data?.pages || []).flatMap((page) => page.items || []),
        [openQuery.data?.pages],
    );
    const historyItems = useMemo(
        () => (historyQuery.data?.pages || []).flatMap((page) => page.items || []),
        [historyQuery.data?.pages],
    );

    const asks = useMemo(() => groupIdenticalAsks(openItems), [openItems]);
    const hoistedReason = useMemo(() => sharedUndeliverableReason(openItems), [openItems]);
    const [today, earlier] = useMemo(
        () => [
            historyItems.filter((item) => isFromToday(item.created_at)),
            historyItems.filter((item) => !isFromToday(item.created_at)),
        ],
        [historyItems],
    );

    const isLoading = openQuery.isLoading;
    const askCount = asks.length;
    // Counted off what loaded, so it is a floor, not a total — said with a "+"
    // when there is another page rather than stating a number nobody counted.
    const askLabel = isLoading || askCount === 0
        ? null
        : openQuery.hasNextPage
            ? `${askCount}+`
            : String(askCount);

    return (
        <div className="notification-page">
            <div className="notification-page-controls">
                <Tabs value={scope} onValueChange={(value) => setScope(value as NotificationsScope)}>
                    <TabsList>
                        {SCOPE_TABS.map((tab) => (
                            <TabsTrigger key={tab.value} value={tab.value}>
                                {tab.label}
                                {tab.value === 'open' && askLabel ? (
                                    <span className="notification-tab-count">{askLabel}</span>
                                ) : null}
                            </TabsTrigger>
                        ))}
                    </TabsList>
                </Tabs>
                {unread > 0 ? (
                    <Button
                        type="button"
                        variant="quiet"
                        size="xs"
                        onClick={() => markAllRead.mutate()}
                        disabled={markAllRead.isPending}
                    >
                        Mark all read
                    </Button>
                ) : null}
            </div>

            {/* One missing connection, stated once. Every open ask sharing a
                reason means the pod cannot reach anyone at all — which is a
                thing to go fix, not a footnote to repeat under each card. */}
            {hoistedReason ? (
                <div className="notification-banner">
                    <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
                    <p>
                        Nothing reached a chat app: {hoistedReason} These are waiting here
                        instead.
                    </p>
                    <Link
                        href={`/pod/${encodeURIComponent(podId)}/surfaces`}
                        className="notification-banner-action custom-focus-ring"
                    >
                        Connect a surface
                    </Link>
                </div>
            ) : null}

            {isLoading ? (
                <NotificationsSkeleton />
            ) : asks.length > 0 ? (
                <div className="notification-card-stack">
                    {asks.map((group) => (
                        <NotificationAskCard
                            key={group.key}
                            group={group}
                            podId={podId}
                            hoistedReason={hoistedReason}
                            resolveAgentName={resolveAgentName}
                            resolveFlowName={resolveFlowName}
                        />
                    ))}
                </div>
            ) : (
                <div className="notification-empty">
                    <p>Nothing needs you right now.</p>
                    {/* The explanation belongs where the page is otherwise blank.
                        Under "Everything" there is a ledger right below saying
                        the same thing by example. */}
                    {scope === 'open' ? (
                        <p>
                            Agents and workflows leave a note here when they need an answer,
                            and send it to your chat apps at the same time.
                        </p>
                    ) : null}
                </div>
            )}

            {openQuery.hasNextPage ? (
                <div className="notification-page-more">
                    <Button
                        type="button"
                        variant="quiet"
                        size="sm"
                        onClick={() => openQuery.fetchNextPage()}
                        disabled={openQuery.isFetchingNextPage}
                    >
                        {openQuery.isFetchingNextPage ? 'Loading…' : 'Load more asks'}
                    </Button>
                </div>
            ) : null}

            {scope === 'all' ? (
                <div className="notification-history">
                    {historyQuery.isLoading ? (
                        <Skeleton className="h-3 w-24" />
                    ) : historyItems.length === 0 ? (
                        <p className="notification-history-empty">
                            Nothing has been answered or dismissed yet.
                        </p>
                    ) : (
                        <>
                            <NotificationLedger
                                band="Today"
                                items={today}
                                podId={podId}
                                expandedId={expandedId}
                                onToggle={(id) =>
                                    setExpandedId((current) => (current === id ? null : id))
                                }
                                resolveAgentName={resolveAgentName}
                                deepLinkedId={deepLinkedId}
                            />
                            <NotificationLedger
                                band="Earlier"
                                items={earlier}
                                podId={podId}
                                expandedId={expandedId}
                                onToggle={(id) =>
                                    setExpandedId((current) => (current === id ? null : id))
                                }
                                resolveAgentName={resolveAgentName}
                                deepLinkedId={deepLinkedId}
                            />
                        </>
                    )}

                    {historyQuery.hasNextPage ? (
                        <div className="notification-page-more">
                            <Button
                                type="button"
                                variant="quiet"
                                size="sm"
                                onClick={() => historyQuery.fetchNextPage()}
                                disabled={historyQuery.isFetchingNextPage}
                            >
                                {historyQuery.isFetchingNextPage ? 'Loading…' : 'Load older'}
                            </Button>
                        </div>
                    ) : null}
                </div>
            ) : null}
        </div>
    );
}
