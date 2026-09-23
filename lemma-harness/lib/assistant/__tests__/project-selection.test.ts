import { describe, expect, it } from 'vitest';

import {
    projectConversationMetadata,
    projectFromMetadata,
    projectLabel,
} from '../project-selection';

describe('projectConversationMetadata', () => {
    it('shapes the selection the way the backend reads it back', () => {
        expect(projectConversationMetadata({
            owner: 'acme',
            repo: 'web',
            ref: 'main',
            accountId: 'acc-1',
        })).toEqual({
            repo: { owner: 'acme', repo: 'web', ref: 'main', account_id: 'acc-1' },
        });
    });

    it('omits an unknown ref and account rather than sending empty strings', () => {
        expect(projectConversationMetadata({ owner: 'acme', repo: 'web' }))
            .toEqual({ repo: { owner: 'acme', repo: 'web' } });
    });

    it('sends nothing at all for a scratchpad conversation', () => {
        // Undefined, not `{}`: an empty object would still be a deliberate
        // statement, and the caller falls through to its own metadata instead.
        expect(projectConversationMetadata(null)).toBeUndefined();
    });
});

describe('projectFromMetadata', () => {
    it('reads back what creation stamped', () => {
        expect(projectFromMetadata({
            cwd: '/workspace/repos/acme/web',
            repo: { owner: 'acme', repo: 'web', ref: 'main', account_id: 'acc-1' },
            repo_full_name: 'acme/web',
        })).toEqual({ owner: 'acme', repo: 'web', ref: 'main', accountId: 'acc-1' });
    });

    it('treats a scratchpad conversation as having no project', () => {
        expect(projectFromMetadata({ cwd: '/workspace/c/2026-08-08/ab3f2k7q' })).toBeNull();
        expect(projectFromMetadata(null)).toBeNull();
        expect(projectFromMetadata(undefined)).toBeNull();
    });

    it('ignores a repo missing either half of its name', () => {
        expect(projectFromMetadata({ repo: { owner: 'acme' } })).toBeNull();
        expect(projectFromMetadata({ repo: { repo: 'web' } })).toBeNull();
        expect(projectFromMetadata({ repo: 'acme/web' })).toBeNull();
    });
});

describe('projectLabel', () => {
    it('names a project the way GitHub does', () => {
        expect(projectLabel({ owner: 'acme', repo: 'web' })).toBe('acme/web');
    });
});
