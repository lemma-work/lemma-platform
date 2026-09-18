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
    // Bounded. The server caps these sets well below the ceiling, so reaching
    // it means the token is not advancing -- and an unbounded follow turns
    // that into a page that never settles rather than a short list. The CLI
    // had the same loop and hung a CI job for its whole thirty-minute budget
    // against a stub whose every field answered truthily.
    for (let page = 0; page < 50; page += 1) {
        const answer = await fetch(pageToken);
        items.push(...answer.items);
        if (!answer.items.length || !answer.next_page_token) break;
        pageToken = answer.next_page_token;
    }
    return { items };
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
