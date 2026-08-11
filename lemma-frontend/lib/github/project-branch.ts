/**
 * The branch a conversation works on, and the pull request that branch is in.
 *
 * A project answers "which repository"; this answers "and where in it". Both
 * come back through connector operations, so what arrives is GitHub's own JSON
 * inside the execution envelope (`{ result }`) — these read it into the two
 * shapes the UI actually renders, and nothing wider.
 *
 * This is *pushed* state. It is what GitHub knows, not what is on disk in the
 * agent's checkout: a branch with uncommitted work looks identical here to one
 * without. Every label built from it has to stay true under that reading.
 *
 * Kept free of React so the parsing is testable on its own.
 */

import type { OperationExecutionResponse } from 'lemma-sdk';

/** GitHub splits this across three fields; one closed set is easier to render. */
export type PullRequestState = 'draft' | 'open' | 'merged' | 'closed';

export interface ProjectPullRequest {
    number: number;
    title: string;
    url: string;
    state: PullRequestState;
    base: string;
    head: string;
    /**
     * Only the detail call carries these — the list endpoint omits them
     * entirely, so absent means "not fetched yet", never "no changes".
     */
    additions?: number;
    deletions?: number;
    changedFiles?: number;
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
    value && typeof value === 'object' && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;

const asString = (value: unknown): string | undefined =>
    typeof value === 'string' && value.trim() ? value : undefined;

const asCount = (value: unknown): number | undefined =>
    typeof value === 'number' && Number.isFinite(value) ? value : undefined;

/** `repos_list_branches` — names only; the picker never shows anything else. */
export const branchNamesFromExecution = (
    response: OperationExecutionResponse | undefined,
): string[] => {
    const items = response?.result;
    if (!Array.isArray(items)) return [];
    return items
        .map((item) => asString(asRecord(item)?.name))
        .filter((name): name is string => Boolean(name));
};

/** `repos_get` — the base a new pull request should target. */
export const defaultBranchFromExecution = (
    response: OperationExecutionResponse | undefined,
): string | undefined => asString(asRecord(response?.result)?.default_branch);

/**
 * The branch's own branch first, then the repo's default, then the rest.
 *
 * Both are one click away from being what you want next, and a list of two
 * hundred alphabetical branches buries them.
 */
export const orderBranches = (
    names: string[],
    options: { current?: string; defaultBranch?: string } = {},
): string[] => {
    const pinned: string[] = [];
    for (const name of [options.current, options.defaultBranch]) {
        // A conversation sitting on the default branch names it twice; pinning
        // it twice would render the same branch twice in the list.
        if (name && names.includes(name) && !pinned.includes(name)) pinned.push(name);
    }
    return [...pinned, ...names.filter((name) => !pinned.includes(name))];
};

const pullRequestState = (record: Record<string, unknown>): PullRequestState => {
    // `merged_at` is the only honest signal: a merged pull request reports
    // `state: "closed"` like an abandoned one, and the two mean opposite things.
    if (asString(record.merged_at)) return 'merged';
    if (record.draft === true) return 'draft';
    return record.state === 'closed' ? 'closed' : 'open';
};

const toPullRequest = (value: unknown): ProjectPullRequest | null => {
    const record = asRecord(value);
    const number = asCount(record?.number);
    if (!record || number === undefined) return null;
    return {
        number,
        title: asString(record.title) || `#${number}`,
        url: asString(record.html_url) || '',
        state: pullRequestState(record),
        base: asString(asRecord(record.base)?.ref) || '',
        head: asString(asRecord(record.head)?.ref) || '',
        additions: asCount(record.additions),
        deletions: asCount(record.deletions),
        changedFiles: asCount(record.changed_files),
    };
};

/**
 * `pulls_list` for one branch, newest first — the pull request that branch is
 * currently in. A branch reopened after a merge has two; the caller asked for
 * the most recently updated, and that is the one worth showing.
 */
export const pullRequestFromExecution = (
    response: OperationExecutionResponse | undefined,
): ProjectPullRequest | null => {
    const items = response?.result;
    if (!Array.isArray(items)) return null;
    for (const item of items) {
        const pullRequest = toPullRequest(item);
        if (pullRequest) return pullRequest;
    }
    return null;
};

/** `pulls_get` or `pulls_create` — a single pull request, totals included. */
export const pullRequestDetailFromExecution = (
    response: OperationExecutionResponse | undefined,
): ProjectPullRequest | null => toPullRequest(response?.result);

export const pullRequestStateLabel = (state: PullRequestState): string =>
    ({ draft: 'Draft', open: 'Open', merged: 'Merged', closed: 'Closed' })[state];

/**
 * A diffstat only when both halves are known. Rendering `+0 −0` for a pull
 * request whose totals never arrived states something false about the branch.
 */
export const pullRequestDiffstat = (
    pullRequest: ProjectPullRequest,
): { additions: number; deletions: number } | null =>
    pullRequest.additions === undefined || pullRequest.deletions === undefined
        ? null
        : { additions: pullRequest.additions, deletions: pullRequest.deletions };

/** GitHub's compare view for a branch against its base. */
export const compareBranchUrl = (
    owner: string,
    repo: string,
    base: string,
    head: string,
): string =>
    `https://github.com/${owner}/${repo}/compare/${encodeURIComponent(base)}...${encodeURIComponent(head)}`;

/** A branch name read as a sentence: the title a person would have typed. */
export const pullRequestTitleFromBranch = (branch: string): string => {
    const words = branch
        .split('/')
        .pop()!
        .replace(/[-_]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
    if (!words) return branch;
    return words.charAt(0).toUpperCase() + words.slice(1);
};
