'use client';

/**
 * The repositories a user can start a conversation in.
 *
 * A "project" is a GitHub repo the agent works inside: the conversation carries
 * `metadata.repo`, the backend derives its working directory from it and clones
 * it before the first command runs. This hook answers the two questions the
 * composer needs before it can offer that — is GitHub connected, and which
 * repos does this account have — and nothing else.
 *
 * The list comes from the connector's own curated `repos_list_for_authenticated_user`
 * operation rather than a bespoke endpoint, so it is exactly the access the
 * agent will have when it clones. If the operation can't list a repo, the agent
 * couldn't have cloned it either, and offering it would be a lie.
 */

import { useQuery } from '@tanstack/react-query';
import { useCallback } from 'react';
import type { OperationExecutionResponse } from 'lemma-sdk';

import { useOrganization } from '@/components/dashboard/org-context';
import { findDefaultInstallName, useAccounts, useAuthConfigs } from '@/lib/hooks/use-connectors';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

export const GITHUB_CONNECTOR_ID = 'github';

/** Enough to fill a picker without paging; beyond this, search is the answer. */
const REPO_PAGE_SIZE = 100;

export interface GithubProject {
    owner: string;
    repo: string;
    fullName: string;
    /** The repo's own default branch, so a picked project starts where the repo does. */
    ref?: string;
    private: boolean;
    /** Last push, used only for ordering — the newest thing you touched first. */
    updatedAt?: string;
}

interface RepoListItem {
    name?: string;
    full_name?: string;
    private?: boolean;
    default_branch?: string;
    pushed_at?: string;
    updated_at?: string;
    owner?: { login?: string };
}

const toProject = (item: RepoListItem): GithubProject | null => {
    const owner = item.owner?.login || item.full_name?.split('/')[0];
    const repo = item.name || item.full_name?.split('/')[1];
    if (!owner || !repo) return null;
    return {
        owner,
        repo,
        fullName: `${owner}/${repo}`,
        ref: item.default_branch || undefined,
        private: Boolean(item.private),
        updatedAt: item.pushed_at || item.updated_at || undefined,
    };
};

/**
 * An execution answers with `{ result }`: the operation's own response body,
 * which for this one is GitHub's array of repos. Anything else — an error
 * payload, a shape we have never seen — lists nothing rather than guessing.
 */
export const projectsFromExecution = (
    response: OperationExecutionResponse | undefined,
): GithubProject[] => {
    const items = response?.result;
    if (!Array.isArray(items)) return [];
    return (items as RepoListItem[])
        .map(toProject)
        .filter((project): project is GithubProject => project !== null);
};

/**
 * `payload` is passed through to the operation, so these are GitHub's own
 * parameter names. Sorted by push date because the repo you worked in an hour
 * ago is overwhelmingly the one you mean now.
 */
const LIST_PAYLOAD = { per_page: REPO_PAGE_SIZE, sort: 'pushed', affiliation: 'owner,collaborator,organization_member' };

/**
 * The one GitHub account this workspace acts as, and the way to use it.
 *
 * Every GitHub read the UI does — repos, branches, the pull request on a branch
 * — goes through the same three facts: which org, which install, which account.
 * Resolving them once here is what lets the callers below be about GitHub
 * instead of about connector plumbing.
 */
export interface GithubConnection {
    organizationId?: string;
    authConfigName?: string;
    accountId?: string;
    /** True once we know an account exists. */
    isConnected: boolean;
    /** False only while we don't yet know; keeps chips from flickering. */
    isResolved: boolean;
    /** All three facts are in hand, so an operation can actually run. */
    canExecute: boolean;
    execute: (
        operationName: string,
        payload: Record<string, unknown>,
    ) => Promise<OperationExecutionResponse>;
}

export function useGithubConnection(options?: { enabled?: boolean }): GithubConnection {
    const enabled = options?.enabled ?? true;
    const { currentOrg } = useOrganization();
    const organizationId = currentOrg?.id;

    const { data: accounts = [], isFetched: accountsFetched } = useAccounts({
        organizationId,
        connectorId: GITHUB_CONNECTOR_ID,
        limit: 50,
        enabled: enabled && Boolean(organizationId),
    });
    const { data: authConfigs = [] } = useAuthConfigs({
        organizationId,
        limit: 200,
        enabled: enabled && Boolean(organizationId),
    });

    const account = accounts[0];
    const authConfigName = findDefaultInstallName(authConfigs, GITHUB_CONNECTOR_ID);
    const accountId = account?.id;
    const canExecute = Boolean(enabled && organizationId && accountId && authConfigName);

    const execute = useCallback(
        (operationName: string, payload: Record<string, unknown>) =>
            getLemmaClient().connectors.operations.execute(
                {
                    organizationId: organizationId as string,
                    authConfigName: authConfigName as string,
                },
                operationName,
                payload,
                accountId,
            ),
        [organizationId, authConfigName, accountId],
    );

    return {
        organizationId,
        authConfigName,
        accountId,
        isConnected: Boolean(accountId),
        isResolved: Boolean(organizationId) && accountsFetched,
        canExecute,
        execute,
    };
}

export interface GithubProjectsState {
    /** True once we know an account exists — the picker offers repos. */
    isConnected: boolean;
    /** False only while we don't yet know; keeps the chip from flickering. */
    isResolved: boolean;
    accountId?: string;
    projects: GithubProject[];
    isLoadingProjects: boolean;
    /** A failed list is worth saying out loud: it usually means revoked access. */
    error: unknown;
}

export function useGithubProjects(options?: { enabled?: boolean }): GithubProjectsState {
    const connection = useGithubConnection(options);
    const { organizationId, authConfigName, accountId, canExecute, execute } = connection;

    const { data, isLoading, error } = useQuery({
        queryKey: ['github-projects', organizationId, authConfigName, accountId],
        queryFn: async () =>
            projectsFromExecution(
                await execute('repos_list_for_authenticated_user', LIST_PAYLOAD),
            ),
        enabled: canExecute,
        // Repos change far more slowly than a picker gets opened.
        staleTime: 5 * 60 * 1000,
    });

    return {
        isConnected: connection.isConnected,
        isResolved: connection.isResolved,
        accountId,
        projects: data ?? [],
        isLoadingProjects: canExecute && isLoading,
        error,
    };
}
