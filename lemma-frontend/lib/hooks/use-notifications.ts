'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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

export const useNotifications = (podId: string | undefined, limit = 30) =>
    useQuery({
        queryKey: [LIST_KEY, podId, limit],
        queryFn: async (): Promise<NotificationPage> => {
            const response = await getLemmaClient(podId).notifications.list({ limit });
            return response as unknown as NotificationPage;
        },
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

const useNotificationRefresh = (podId: string | undefined) => {
    const queryClient = useQueryClient();
    return () => {
        void queryClient.invalidateQueries({ queryKey: [LIST_KEY, podId] });
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
