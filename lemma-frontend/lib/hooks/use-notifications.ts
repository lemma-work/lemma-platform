'use client';

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

/**
 * Two independent states, and reading them as one is the usual mistake.
 * `status` is about the person; `delivery_status` is about the channel. A
 * notification can be DELIVERED and still OPEN (they haven't answered), or
 * UNDELIVERABLE and still RESPONDED (they saw it here and replied here).
 */
export type NotificationStatus =
    | 'OPEN'
    | 'RESPONDED'
    | 'ACKNOWLEDGED'
    | 'EXPIRED'
    | 'CANCELLED';

export type NotificationDeliveryStatus =
    | 'PENDING'
    | 'DELIVERED'
    | 'UNDELIVERABLE'
    | 'FAILED';

export type Notification = {
    id: string;
    pod_id: string;
    title: string;
    body: string;
    origin_kind: 'AGENT_RUN' | 'WORKFLOW_FORM' | 'SCHEDULE' | 'API';
    origin_id?: string | null;
    origin_conversation_id?: string | null;
    actor_user_id?: string | null;
    actor_agent_id?: string | null;
    status: NotificationStatus;
    delivery_status: NotificationDeliveryStatus;
    expects_response: boolean;
    /** Whether to offer an action at all. */
    awaiting_response: boolean;
    /** Whether that action is a workflow form rather than a text reply. */
    responds_through_action: boolean;
    action?: Record<string, unknown> | null;
    delivery_platform?: string | null;
    delivery_conversation_id?: string | null;
    /** Why no channel could carry it — worth showing, it is usually fixable. */
    undeliverable_reason?: string | null;
    response_summary?: string | null;
    created_at: string;
    expires_at?: string | null;
    delivered_at?: string | null;
    read_at?: string | null;
    responded_at?: string | null;
};

export type NotificationPage = {
    items: Notification[];
    limit: number;
    next_page_token?: string | null;
};

const LIST_KEY = 'notifications';
const COUNT_KEY = 'notifications-unread-count';

/**
 * The statuses that mean a person still owes this notification something —
 * either an answer or an acknowledgement. Everything else is history.
 *
 * Kept here rather than spelled out at each call site so the bell, home and the
 * notifications page agree on what "needs you" means. They disagreeing by one
 * status is how a count and the list under it start contradicting each other.
 */
export const UNATTENDED_NOTIFICATION_STATUSES: NotificationStatus[] = ['OPEN'];

/**
 * Everything that is no longer owed anything — the history half of the inbox.
 *
 * Deliberately the complement of the list above rather than "no filter": the
 * page draws open asks and closed ones in two different shapes, and a history
 * list that also carried the open ones would print every one of them twice.
 * Asked of the API rather than filtered here so that paging counts the rows
 * that actually get drawn.
 */
export const CLOSED_NOTIFICATION_STATUSES: NotificationStatus[] = [
    'RESPONDED',
    'ACKNOWLEDGED',
    'EXPIRED',
    'CANCELLED',
];

/**
 * The SDK types this filter as its generated `enum`, which a string union is not
 * assignable to even where every member matches — and the enum is not re-exported
 * from the package root, so there is nothing to import. The values are the
 * strings either way; the union above is what the rest of the app reads.
 */
type SdkNotificationStatusFilter = Parameters<
    ReturnType<typeof getLemmaClient>['notifications']['list']
>[0] extends { status?: infer TStatus } | undefined
    ? TStatus
    : never;

const toStatusFilter = (status: NotificationStatus[] | undefined) =>
    status as unknown as SdkNotificationStatusFilter;

export const useNotifications = (
    podId: string | undefined,
    { limit = 30, status }: { limit?: number; status?: NotificationStatus[] } = {},
) =>
    useQuery({
        queryKey: [LIST_KEY, podId, limit, status ?? null],
        queryFn: async (): Promise<NotificationPage> => {
            const response = await getLemmaClient(podId).notifications.list({
                limit,
                status: toStatusFilter(status),
            });
            return response as unknown as NotificationPage;
        },
        enabled: !!podId,
    });

/**
 * The same list, paged. The page shows everything a pod has ever asked of you,
 * which is unbounded — the popover's single fixed-size read cannot stand in for
 * it, and loading all of it up front to render thirty rows would be worse.
 */
export const useInfiniteNotifications = (
    podId: string | undefined,
    { limit = 40, status }: { limit?: number; status?: NotificationStatus[] } = {},
) =>
    useInfiniteQuery({
        queryKey: [LIST_KEY, 'infinite', podId, limit, status ?? null],
        queryFn: async ({ pageParam }): Promise<NotificationPage> => {
            const response = await getLemmaClient(podId).notifications.list({
                limit,
                status: toStatusFilter(status),
                pageToken: pageParam || undefined,
            });
            return response as unknown as NotificationPage;
        },
        initialPageParam: undefined as string | undefined,
        getNextPageParam: (lastPage) => lastPage.next_page_token || undefined,
        enabled: !!podId,
    });

/**
 * Polled rather than pushed. A badge that is a minute stale costs nothing, and
 * a realtime subscription for a number is a lot of moving parts to keep alive
 * across tabs. Revisit if the delay is ever actually felt.
 */
export const useUnreadNotificationCount = (
    podId: string | undefined,
    pollIntervalMs = 60_000,
) =>
    useQuery({
        queryKey: [COUNT_KEY, podId],
        queryFn: async (): Promise<number> => {
            const response = await getLemmaClient(podId).notifications.unreadCount();
            return (response as unknown as { unread: number }).unread ?? 0;
        },
        enabled: !!podId,
        refetchInterval: pollIntervalMs,
    });

/**
 * Invalidated on the bare list key, not on `[LIST_KEY, podId]`. The same
 * notification is now read through several shapes — the popover's peek, the
 * page's status-filtered infinite list, home's unattended slice — and they key
 * on the filter, so a prefix that pins `podId` in second position misses every
 * one of them. Answering something and watching it sit there unchanged is the
 * bug that costs more than the extra refetch.
 */
const useNotificationRefresh = (podId: string | undefined) => {
    const queryClient = useQueryClient();
    return () => {
        void queryClient.invalidateQueries({ queryKey: [LIST_KEY] });
        void queryClient.invalidateQueries({ queryKey: [COUNT_KEY, podId] });
    };
};

export const useMarkNotificationRead = (podId: string | undefined) => {
    const refresh = useNotificationRefresh(podId);
    return useMutation({
        mutationFn: (notificationId: string) =>
            getLemmaClient(podId).notifications.markRead(notificationId),
        onSuccess: refresh,
    });
};

export const useMarkAllNotificationsRead = (podId: string | undefined) => {
    const refresh = useNotificationRefresh(podId);
    return useMutation({
        mutationFn: () => getLemmaClient(podId).notifications.markAllRead(),
        onSuccess: refresh,
    });
};

/**
 * Answering from the app. Produces the same RESPONDED an agent-mediated reply
 * on Slack or Telegram produces, so whoever asked reads one thing either way.
 *
 * Rejects with 409 when someone already answered — surface that rather than
 * swallowing it, because the user is looking at a stale row and needs to know
 * the list moved under them.
 */
export const useRespondToNotification = (podId: string | undefined) => {
    const refresh = useNotificationRefresh(podId);
    return useMutation({
        mutationFn: ({
            notificationId,
            summary,
        }: {
            notificationId: string;
            summary: string;
        }) =>
            getLemmaClient(podId).notifications.respond(notificationId, { summary }),
        onSuccess: refresh,
    });
};

export const useAcknowledgeNotification = (podId: string | undefined) => {
    const refresh = useNotificationRefresh(podId);
    return useMutation({
        mutationFn: (notificationId: string) =>
            getLemmaClient(podId).notifications.acknowledge(notificationId),
        onSuccess: refresh,
    });
};

/**
 * The same ask, asked N times, settled in one gesture.
 *
 * Six identical check-ins are six records with six runs waiting on them, and
 * answering only the newest leaves five agents still waiting for something the
 * person has already said. The inbox collapses them into one card, so the card's
 * one button has to close all of them.
 *
 * Sequential and per-item tolerant on purpose. A 409 means somebody already
 * answered that one — true of a single row in a group all the time — and
 * `Promise.all` would abandon the rest of the group over it. The failure that
 * matters is *all* of them failing, which is the only case this rejects on.
 */
const settleEach = async (
    ids: string[],
    settle: (notificationId: string) => Promise<unknown>,
) => {
    let settled = 0;
    let lastError: unknown = null;
    for (const id of ids) {
        try {
            await settle(id);
            settled += 1;
        } catch (error) {
            lastError = error;
        }
    }
    if (settled === 0 && lastError) throw lastError;
    return settled;
};

/**
 * Reading a collapsed run reads all of it. The inbox draws six identical asks as
 * one card, so a person who has looked at that card has seen all six — leaving
 * five of them unread lights a badge for something already read.
 */
export const useMarkNotificationsRead = (podId: string | undefined) => {
    const refresh = useNotificationRefresh(podId);
    return useMutation({
        mutationFn: (notificationIds: string[]) =>
            settleEach(notificationIds, (id) =>
                getLemmaClient(podId).notifications.markRead(id),
            ),
        onSuccess: refresh,
    });
};

export const useAcknowledgeNotifications = (podId: string | undefined) => {
    const refresh = useNotificationRefresh(podId);
    return useMutation({
        mutationFn: (notificationIds: string[]) =>
            settleEach(notificationIds, (id) =>
                getLemmaClient(podId).notifications.acknowledge(id),
            ),
        onSuccess: refresh,
    });
};

export const useRespondToNotifications = (podId: string | undefined) => {
    const refresh = useNotificationRefresh(podId);
    return useMutation({
        mutationFn: ({
            notificationIds,
            summary,
        }: {
            notificationIds: string[];
            summary: string;
        }) =>
            settleEach(notificationIds, (id) =>
                getLemmaClient(podId).notifications.respond(id, { summary }),
            ),
        onSuccess: refresh,
    });
};
