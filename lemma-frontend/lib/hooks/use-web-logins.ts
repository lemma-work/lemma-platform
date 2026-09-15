'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { WebLogin, WebLoginAuditEntry } from 'lemma-sdk';

import { getLemmaClient } from '@/lib/sdk/lemma-client';

export const webLoginsQueryKey = () => ['web-logins'] as const;
export const webLoginHistoryQueryKey = () => ['web-logins', 'history'] as const;

export const useWebLogins = () =>
    useQuery<{ items: WebLogin[] }>({
        queryKey: webLoginsQueryKey(),
        queryFn: () => getLemmaClient().webLogins.list(),
        staleTime: 10_000,
    });

export const useWebLoginHistory = (enabled: boolean) =>
    useQuery<{ items: WebLoginAuditEntry[] }>({
        queryKey: webLoginHistoryQueryKey(),
        queryFn: () => getLemmaClient().webLogins.history(50),
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
