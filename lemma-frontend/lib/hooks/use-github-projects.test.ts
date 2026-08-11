import { describe, expect, it } from 'vitest';

import { projectsFromExecution } from './use-github-projects';

/** One repo as GitHub returns it, trimmed to the fields the picker reads. */
const repo = {
    name: 'lemma-platform',
    full_name: 'lemma-work/lemma-platform',
    private: false,
    default_branch: 'main',
    pushed_at: '2026-08-11T09:58:09Z',
    updated_at: '2026-08-11T09:58:32Z',
    owner: { login: 'lemma-work' },
};

describe('projects from a connector execution', () => {
    it('reads the repos out of the result envelope', () => {
        expect(projectsFromExecution({ result: [repo] })).toEqual([
            {
                owner: 'lemma-work',
                repo: 'lemma-platform',
                fullName: 'lemma-work/lemma-platform',
                ref: 'main',
                private: false,
                updatedAt: '2026-08-11T09:58:09Z',
            },
        ]);
    });

    it('falls back to the full name when the owner block is missing', () => {
        expect(
            projectsFromExecution({
                result: [{ full_name: 'lemma-work/lemma-app', private: true }],
            }),
        ).toEqual([
            {
                owner: 'lemma-work',
                repo: 'lemma-app',
                fullName: 'lemma-work/lemma-app',
                ref: undefined,
                private: true,
                updatedAt: undefined,
            },
        ]);
    });

    it('drops entries that name no repo', () => {
        expect(projectsFromExecution({ result: [{ private: false }, repo] })).toHaveLength(1);
    });

    it.each([
        ['no response', undefined],
        ['an empty envelope', { result: undefined }],
        ['a non-list result', { result: { message: 'Bad credentials' } }],
    ])('lists nothing for %s', (_label, response) => {
        expect(projectsFromExecution(response as never)).toEqual([]);
    });
});
