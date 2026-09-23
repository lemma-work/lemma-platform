'use client';

/**
 * What a conversation's branch looks like on GitHub.
 *
 * The project picker answers "which repository". These answer the rest of the
 * question a person asks while an agent works in it: which branch am I on, what
 * else could I switch to, and is there a pull request for this yet.
 *
 * Everything here reads through the same curated connector operations the agent
 * itself would use, so what the panel shows is exactly the access the run has.
 * All of it is *pushed* state — see `lib/github/project-branch`.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
    branchNamesFromExecution,
    defaultBranchFromExecution,
    orderBranches,
    pullRequestDetailFromExecution,
    pullRequestFromExecution,
    type ProjectPullRequest,
} from '@/lib/github/project-branch';
import { useGithubConnection } from '@/lib/hooks/use-github-projects';
import type { ProjectSelection } from '@/lib/assistant/project-selection';

/** Enough to hold any repo a person browses by name; beyond it, search. */
const BRANCH_PAGE_SIZE = 100;

export interface RepoBranchesState {
    /** The current branch first, then the default, then the rest. */
    branches: string[];
    defaultBranch?: string;
    isLoading: boolean;
    error: unknown;
}

export function useRepoBranches(
    project: ProjectSelection | null,
    options?: { enabled?: boolean },
): RepoBranchesState {
    const connection = useGithubConnection({ enabled: options?.enabled ?? true });
    const enabled = Boolean(connection.canExecute && project);

    const { data, isLoading, error } = useQuery({
        queryKey: [
            'github-branches',
            connection.organizationId,
            connection.accountId,
            project?.owner,
            project?.repo,
        ],
        queryFn: async () => {
            const target = { owner: project!.owner, repo: project!.repo };
            // Two calls, one loading state: the default branch is both the base
            // a new pull request wants and the branch worth listing near the top.
            const [branches, repo] = await Promise.all([
                connection.execute('repos_list_branches', {
                    ...target,
                    per_page: BRANCH_PAGE_SIZE,
                }),
                connection.execute('repos_get', target),
            ]);
            return {
                names: branchNamesFromExecution(branches),
                defaultBranch: defaultBranchFromExecution(repo),
            };
        },
        enabled,
        // A branch list goes stale the moment someone pushes, but re-reading it
        // on every popover open costs more than being a minute behind.
        staleTime: 60 * 1000,
    });

    return {
        branches: orderBranches(data?.names ?? [], {
            current: project?.ref,
            defaultBranch: data?.defaultBranch,
        }),
        defaultBranch: data?.defaultBranch,
        isLoading: enabled && isLoading,
        error,
    };
}

export interface BranchPullRequestState {
    /** Null once we've looked and there is none — not the same as loading. */
    pullRequest: ProjectPullRequest | null;
    isLoading: boolean;
    error: unknown;
}

export function useBranchPullRequest(
    project: ProjectSelection | null,
    options?: { enabled?: boolean },
): BranchPullRequestState {
    const connection = useGithubConnection({ enabled: options?.enabled ?? true });
    const branch = project?.ref;
    const enabled = Boolean(connection.canExecute && project && branch);

    const { data, isLoading, error } = useQuery({
        queryKey: pullRequestQueryKey(connection.organizationId, project),
        queryFn: async () => {
            const target = { owner: project!.owner, repo: project!.repo };
            const listed = pullRequestFromExecution(
                await connection.execute('pulls_list', {
                    ...target,
                    // `user:ref` is the filter's own format, and `all` because a
                    // merged pull request is the most useful thing to say about
                    // a branch whose work is already done.
                    head: `${project!.owner}:${branch}`,
                    state: 'all',
                    sort: 'updated',
                    direction: 'desc',
                    per_page: 1,
                }),
            );
            if (!listed) return null;
            // The list endpoint carries no totals, so the diffstat costs a
            // second call — worth it, since the numbers are half the point.
            const detailed = pullRequestDetailFromExecution(
                await connection.execute('pulls_get', {
                    ...target,
                    pull_number: listed.number,
                }),
            );
            return detailed ?? listed;
        },
        enabled,
        staleTime: 60 * 1000,
    });

    return {
        pullRequest: data ?? null,
        isLoading: enabled && isLoading,
        error,
    };
}

const pullRequestQueryKey = (
    organizationId: string | undefined,
    project: ProjectSelection | null,
) => ['github-branch-pull-request', organizationId, project?.owner, project?.repo, project?.ref];

export interface CreatePullRequestInput {
    title: string;
    base: string;
    body?: string;
    draft?: boolean;
}

/**
 * Opening the pull request for the branch this conversation is on.
 *
 * The one write on this path, and it publishes: it is wired to an explicit
 * submit, never to opening a panel. On success the branch's pull request query
 * is seeded with what GitHub returned, so the chip is right immediately rather
 * than after a refetch.
 */
export function useCreatePullRequest(project: ProjectSelection | null) {
    const connection = useGithubConnection();
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (input: CreatePullRequestInput) => {
            if (!project?.ref) throw new Error('A branch is required to open a pull request');
            return pullRequestDetailFromExecution(
                await connection.execute('pulls_create', {
                    owner: project.owner,
                    repo: project.repo,
                    body: {
                        title: input.title,
                        head: project.ref,
                        base: input.base,
                        ...(input.body ? { body: input.body } : {}),
                        ...(input.draft ? { draft: true } : {}),
                    },
                }),
            );
        },
        onSuccess: (pullRequest) => {
            queryClient.setQueryData(
                pullRequestQueryKey(connection.organizationId, project),
                pullRequest,
            );
        },
    });
}
