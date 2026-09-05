'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '../sdk/lemma-client';
import { appIndexQueryKey } from './use-app';

export interface AppRelease {
    id: string;
    release_number: number;
    /** sha256 of the release's dist archive. */
    version: string;
    label?: string | null;
    created_at?: string | null;
    is_live: boolean;
    has_source: boolean;
    /** Set once retention removed this release's build. */
    pruned_at?: string | null;
    preview_url: string;
}

export const appReleasesQueryKey = (podId: string, appName: string) =>
    ['app-releases', podId, appName] as const;

export function useAppReleases(podId: string, appName: string | null, enabled = true) {
    return useQuery({
        queryKey: appReleasesQueryKey(podId, appName ?? ''),
        enabled: Boolean(podId && appName) && enabled,
        queryFn: async (): Promise<AppRelease[]> => {
            const response = await getLemmaClient(podId).apps.releases(appName as string) as {
                items?: AppRelease[];
            };
            return Array.isArray(response?.items) ? response.items : [];
        },
    });
}

export function usePromoteAppRelease(podId: string, appName: string | null) {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (releaseRef: string) => {
            return getLemmaClient(podId).apps.promoteRelease(appName as string, releaseRef);
        },
        onSuccess: () => {
            // The live pointer moved, so both the release list and the app index
            // (which carries status) are stale.
            void queryClient.invalidateQueries({ queryKey: appReleasesQueryKey(podId, appName ?? '') });
            void queryClient.invalidateQueries({ queryKey: appIndexQueryKey(podId) });
        },
    });
}
