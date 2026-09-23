import { describe, expect, it } from 'vitest';
import { AgentHostStatus } from 'lemma-sdk';
import type { AgentHostResponse } from 'lemma-sdk';

import { isArriving } from '@/lib/hooks/use-agent-runtime';

const host = (overrides: Partial<AgentHostResponse>): AgentHostResponse => ({
        capacity: {},
        created_at: new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString(),
        display_name: 'This computer',
        host_release: '0.7.0',
        id: 'host-1',
        installation_id: 'install-1',
        last_seen_at: null,
        protocol_version: 1,
        revoked_at: null,
        status: AgentHostStatus.OFFLINE,
        updated_at: new Date().toISOString(),
        user_id: 'user-1',
        ...overrides,
});

const secondsAgo = (seconds: number) => new Date(Date.now() - seconds * 1000).toISOString();

describe('isArriving', () => {
    it('leaves an online machine alone', () => {
        expect(isArriving(host({ status: AgentHostStatus.ONLINE, last_seen_at: secondsAgo(1) }))).toBe(false);
});

    it('watches a machine that checked in moments ago', () => {
        // Coming back from a restart or a network blip: the next poll is likely
        // to show it online, which is what the fast interval is for.
        expect(isArriving(host({ last_seen_at: secondsAgo(5) }))).toBe(true);
});

    it('watches a pairing that has never checked in yet', () => {
        // Paired seconds ago from the terminal or the onboarding step; it has
        // nothing to report until its first poll lands.
        expect(isArriving(host({ created_at: secondsAgo(5), last_seen_at: null }))).toBe(true);
});

    it('stops watching a machine that has been quiet for a long time', () => {
        // The regression this exists for. "Not online" was read as "settling",
        // so a computer paired to a workspace it can no longer reach — a local
        // stack that has since stopped, say — kept the Models page fetching
        // twice a second for as long as it stayed open, and the per-host
        // harness queries multiplied that by the number of machines.
        expect(isArriving(host({ last_seen_at: secondsAgo(600) }))).toBe(false);
});

    it('stops watching an old pairing that never checked in at all', () => {
        expect(isArriving(host({ last_seen_at: null }))).toBe(false);
});

    it('treats an unreadable timestamp as quiet rather than arriving', () => {
        // A malformed date must not become an infinite fast poll.
        expect(isArriving(host({ last_seen_at: 'not a date' }))).toBe(false);
});
});
