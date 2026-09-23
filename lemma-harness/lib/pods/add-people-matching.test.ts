import { describe, expect, it } from 'vitest';

import {
    offersInviteFor,
    podMemberEmails,
    resolveAlreadyHere,
    type DraftLike,
    type MemberLike,
} from './add-people-matching';

const PRIYA: MemberLike = {
    user_id: 'u1',
    user_name: 'Priya Raman',
    user_email: 'priya@acme.com',
    email: 'priya@acme.com',
};

describe('add-people matching', () => {
    it('collects both addresses a member is known by', () => {
        // The roster carries `user_email` and `email` separately, and either can
        // be the one somebody types.
        expect(
            podMemberEmails([{ user_id: 'u1', user_email: 'work@acme.com', email: 'billing@acme.com' }]),
        ).toEqual(new Set(['work@acme.com', 'billing@acme.com']));
        expect(podMemberEmails([{ user_id: 'u2' }])).toEqual(new Set());
    });

    it('does not offer to invite somebody already in the pod', () => {
        // The bug this file exists for: the address matched a member, every
        // branch declined it, and the empty state announced that nobody by that
        // name existed.
        expect(offersInviteFor({
            query: 'Priya@Acme.com',
            members: [PRIYA],
            candidateEmails: [],
            draftedEmails: [],
        })).toBe(false);
    });

    it('does not offer to invite a colleague, or somebody already queued', () => {
        const base = { query: 'sam@acme.com', members: [] as MemberLike[] };
        // In the organization already — they get added, never mailed.
        expect(offersInviteFor({ ...base, candidateEmails: ['sam@acme.com'], draftedEmails: [] })).toBe(false);
        // Already a chip in the composer.
        expect(offersInviteFor({ ...base, candidateEmails: [], draftedEmails: ['SAM@acme.com'] })).toBe(false);
        // Nobody has them: this is the one case worth an invite.
        expect(offersInviteFor({ ...base, candidateEmails: [], draftedEmails: [] })).toBe(true);
    });

    it('names the member behind a typed address, and the address behind a typed name', () => {
        expect(resolveAlreadyHere({ query: 'priya@acme.com', members: [PRIYA], drafts: [] })).toEqual([
            { key: 'in-pod:u1', name: 'Priya Raman', note: 'Already a member' },
        ]);
        expect(resolveAlreadyHere({ query: 'priya', members: [PRIYA], drafts: [] })).toEqual([
            { key: 'in-pod:u1', name: 'Priya Raman', note: 'Already a member' },
        ]);
    });

    it('falls back to the address when the member has no name on file', () => {
        expect(
            resolveAlreadyHere({
                query: 'nameless',
                members: [{ user_id: 'u3', user_email: 'nameless@acme.com' }],
                drafts: [],
            })[0],
        ).toEqual({ key: 'in-pod:u3', name: 'nameless@acme.com', note: 'Already a member' });
    });

    it('separates somebody already in the pod from somebody already in the box', () => {
        const drafts: DraftLike[] = [{ key: 'email:sam@acme.com', kind: 'email', email: 'sam@acme.com' }];
        expect(resolveAlreadyHere({ query: 'sam@acme.com', members: [PRIYA], drafts })).toEqual([
            { key: 'queued:email:sam@acme.com', name: 'sam@acme.com', note: 'Already added' },
        ]);
    });

    it('says nothing when the query names nobody, or is empty', () => {
        expect(resolveAlreadyHere({ query: 'nobody', members: [PRIYA], drafts: [] })).toEqual([]);
        expect(resolveAlreadyHere({ query: '   ', members: [PRIYA], drafts: [] })).toEqual([]);
    });

    it('stops at five, so the list stays scannable', () => {
        const members = Array.from({ length: 9 }, (_, index) => ({
            user_id: `u${index}`,
            user_name: `Person ${index}`,
            user_email: `person${index}@acme.com`,
        }));
        expect(resolveAlreadyHere({ query: 'person', members, drafts: [] })).toHaveLength(5);
    });
});
