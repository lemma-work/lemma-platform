'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { WebLogin, WebLoginAuditEntry } from 'lemma-sdk';

import { getLemmaClient } from '@/lib/sdk/lemma-client';

export const webLoginsQueryKey = () => ['web-logins'] as const;
export const webLoginHistoryQueryKey = () => ['web-logins', 'history'] as const;

/**
 * Every page, not the first one.
 *
 * Both of these listings paginate. The screen they feed is where a person
 * revokes a saved login, and a login that is not on screen is one they cannot
 * revoke -- so stopping at the first page would quietly hide the thing this
 * screen exists for. The sets are small and capped server-side, so following
 * the token to the end costs a request or two at most.
 */
async function allPages<T>(
    fetch: (pageToken?: string) => Promise<{ items: T[]; next_page_token: string | null }>,
): Promise<{ items: T[] }> {
    const items: T[] = [];
    let pageToken: string | undefined;
    for (;;) {
        const page = await fetch(pageToken);
        items.push(...page.items);
        if (!page.next_page_token) return { items };
        pageToken = page.next_page_token;
    }
}

export const useWebLogins = () =>
    useQuery<{ items: WebLogin[] }>({
        queryKey: webLoginsQueryKey(),
        queryFn: () =>
            allPages((pageToken) => getLemmaClient().webLogins.list({ pageToken })),
        staleTime: 10_000,
    });

export const useWebLoginHistory = (enabled: boolean) =>
    useQuery<{ items: WebLoginAuditEntry[] }>({
        queryKey: webLoginHistoryQueryKey(),
        queryFn: () =>
            allPages((pageToken) => getLemmaClient().webLogins.history(50, pageToken)),
        enabled,
        staleTime: 10_000,
    });

export const useRemoveWebLogin = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (origin: string) => getLemmaClient().webLogins.remove(origin),
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: webLoginsQueryKey() });
            void queryClient.invalidateQueries({ queryKey: webLoginHistoryQueryKey() });
        },
    });
};
