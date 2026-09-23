import { describe, expect, it } from 'vitest';

import {
    branchNamesFromExecution,
    compareBranchUrl,
    defaultBranchFromExecution,
    orderBranches,
    pullRequestDetailFromExecution,
    pullRequestDiffstat,
    pullRequestFromExecution,
    pullRequestTitleFromBranch,
} from './project-branch';

/** One pull request as `pulls_list` returns it, trimmed to what we read. */
const listed = {
    number: 316,
    title: 'Fix the surface regressions',
    html_url: 'https://github.com/lemma-work/lemma-platform/pull/316',
    state: 'closed',
    draft: false,
    merged_at: '2026-08-11T09:58:09Z',
    base: { ref: 'main' },
    head: { ref: 'fix-303-surface-regressions' },
};

describe('branches', () => {
    it('reads names out of the branch list', () => {
        expect(
            branchNamesFromExecution({
                result: [{ name: 'main' }, { name: 'fix-303' }, { protected: true }],
            }),
        ).toEqual(['main', 'fix-303']);
    });

    it('reads the default branch off the repo', () => {
        expect(defaultBranchFromExecution({ result: { default_branch: 'main' } })).toBe('main');
        expect(defaultBranchFromExecution({ result: [] })).toBeUndefined();
    });

    it('puts the current branch first, then the default', () => {
        expect(
            orderBranches(['alpha', 'main', 'fix-303', 'zeta'], {
                current: 'fix-303',
                defaultBranch: 'main',
            }),
        ).toEqual(['fix-303', 'main', 'alpha', 'zeta']);
    });

    it('ignores pins that are not in the list', () => {
        expect(orderBranches(['main'], { current: 'gone', defaultBranch: 'main' })).toEqual([
            'main',
        ]);
    });

    it('lists the default branch once when that is also the current one', () => {
        expect(
            orderBranches(['alpha', 'main'], { current: 'main', defaultBranch: 'main' }),
        ).toEqual(['main', 'alpha']);
    });
});

describe('the pull request on a branch', () => {
    it('calls a closed-but-merged pull request merged', () => {
        expect(pullRequestFromExecution({ result: [listed] })).toMatchObject({
            number: 316,
            state: 'merged',
            base: 'main',
            head: 'fix-303-surface-regressions',
        });
    });

    it('distinguishes draft, open and abandoned', () => {
        const state = (overrides: Record<string, unknown>) =>
            pullRequestFromExecution({ result: [{ ...listed, merged_at: null, ...overrides }] })
                ?.state;
        expect(state({ draft: true })).toBe('draft');
        expect(state({ state: 'open' })).toBe('open');
        expect(state({ state: 'closed' })).toBe('closed');
    });

    it('is null when the branch has none, and when the call did not answer', () => {
        expect(pullRequestFromExecution({ result: [] })).toBeNull();
        expect(pullRequestFromExecution({ result: { message: 'Not Found' } })).toBeNull();
        expect(pullRequestFromExecution(undefined)).toBeNull();
    });

    it('takes totals from the detail call', () => {
        const detailed = pullRequestDetailFromExecution({
            result: { ...listed, additions: 1662, deletions: 575, changed_files: 3 },
        });
        expect(pullRequestDiffstat(detailed!)).toEqual({ additions: 1662, deletions: 575 });
    });

    it('shows no diffstat when the totals never arrived', () => {
        // +0 −0 would be a claim about the branch; absent is the truth.
        expect(pullRequestDiffstat(pullRequestFromExecution({ result: [listed] })!)).toBeNull();
    });
});

describe('what the panel links to', () => {
    it('compares head against base', () => {
        expect(compareBranchUrl('lemma-work', 'lemma-platform', 'main', 'fix/303')).toBe(
            'https://github.com/lemma-work/lemma-platform/compare/main...fix%2F303',
        );
    });

    it('reads a branch name as a title', () => {
        expect(pullRequestTitleFromBranch('fix-303-surface-regressions')).toBe(
            'Fix 303 surface regressions',
        );
        expect(pullRequestTitleFromBranch('deepak/add_branch_picker')).toBe(
            'Add branch picker',
        );
    });
});
