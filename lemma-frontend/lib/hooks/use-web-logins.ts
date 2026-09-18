'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { WebLogin } from 'lemma-sdk';

import { getLemmaClient } from '@/lib/sdk/lemma-client';

export const webLoginsQueryKey = (wake: boolean) => ['web-logins', wake] as const;

/**
 * The sites this person's sandbox browser is signed in to.
 *
 * Read from the browser, not from a table, so there is nothing to page
 * through: it is what one browser is holding. The helper that followed
 * `next_page_token` to exhaustion went with the table -- along with the
 * history listing, which had nothing left to record once no server-side copy
 * of a login existed to do anything to.
 *
 * `wake` is off until somebody asks. A paused computer answers `sleeping`
 * rather than being started, because opening a settings page should not be
 * what spins one up -- and unlike the old table read, this one costs a round
 * trip into the sandbox.
 */
export const useWebLogins = (wake = false) =>
    useQuery<{ items: WebLogin[]; sleeping: boolean }>({
        queryKey: webLoginsQueryKey(wake),
        queryFn: () => getLemmaClient().webLogins.list({ wake }),
        staleTime: 10_000,
    });

export const useRemoveWebLogin = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (origin: string) => getLemmaClient().webLogins.remove(origin),
        onSuccess: () => {
            // Both keys: forgetting is done from the woken list, and the
            // sleeping one is what the page renders on the next visit.
            void queryClient.invalidateQueries({ queryKey: ['web-logins'] });
        },
    });
};
