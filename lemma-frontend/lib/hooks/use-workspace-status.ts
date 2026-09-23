'use client';

import { useQuery } from '@tanstack/react-query';

import { getLemmaClient } from '@/lib/sdk/lemma-client';

export type WorkspaceStatus = Awaited<ReturnType<ReturnType<typeof getLemmaClient>['workspace']['status']>>;

export const workspaceStatusQueryKey = ['workspace-status'] as const;

/** Whether this person's computer is coming up. `state` is "ready" once it is. */
export const isComingUp = (status: WorkspaceStatus | undefined): boolean =>
    status?.state === 'downloading' || status?.state === 'starting';

/**
 * The person's computer, asked about without starting it.
 *
 * Slow while nothing is happening, quick while it is coming up: a download
 * after an update takes minutes, and the indicator should leave the moment it
 * is done rather than up to a slow poll later.
 */
export const useWorkspaceStatus = () =>
    useQuery<WorkspaceStatus>({
        queryKey: workspaceStatusQueryKey,
        queryFn: () => getLemmaClient().workspace.status(),
        refetchInterval: (query) => (isComingUp(query.state.data) ? 3_000 : 15_000),
        refetchOnWindowFocus: true,
        retry: false,
    });
