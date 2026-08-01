'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '../sdk/lemma-client';

/**
 * The in-app inbox.
 *
 * Every proactive message an agent sends writes here, whether or not a chat
 * platform also took it — so this is the one place that always has the whole
 * story, and the reason someone who has never touched Telegram still hears from
 * their agents.
 */

const NOTIFICATIONS_KEY = ['notifications'] as const;

// Agents notify on their own schedule, so the badge has to find out on its own.
// A minute is often enough to feel live without turning an idle tab into a
// polling loop; anything arriving in between still shows the moment the list is
// opened, because opening refetches.
const UNREAD_POLL_MS = 60_000;

export function useUnreadNotificationCount(podId?: string) {
    return useQuery({
        queryKey: [...NOTIFICATIONS_KEY, 'unread-count', podId ?? null],
        queryFn: () => getLemmaClient().notifications.unreadCount(podId),
        refetchInterval: UNREAD_POLL_MS,
        refetchOnWindowFocus: true,
    });
}

export function useNotifications({
    podId,
    unreadOnly = false,
    limit = 30,
    enabled = true,
}: {
    podId?: string;
    unreadOnly?: boolean;
    limit?: number;
    enabled?: boolean;
} = {}) {
    return useQuery({
        queryKey: [...NOTIFICATIONS_KEY, 'list', podId ?? null, unreadOnly, limit],
        queryFn: () => getLemmaClient().notifications.list({ podId, unreadOnly, limit }),
        enabled,
    });
}

export function useMarkNotificationRead() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (notificationId: string) =>
            getLemmaClient().notifications.markRead(notificationId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY });
        },
    });
}

export function useMarkAllNotificationsRead(podId?: string) {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: () => getLemmaClient().notifications.markAllRead(podId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY });
        },
    });
}
