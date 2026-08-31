import { describe, expect, it } from 'vitest';

import {
    describeConnection,
    describeReach,
    surfaceAnswersDirectMessages,
    surfaceReaches,
    surfaceReachesDefaultAgent,
} from '@/lib/utils/surfaces';
import type { AssistantSurface } from '@/lib/types';

type Route = { channel_id?: string; channel_name?: string };

/**
 * A Slack surface: one workspace install, one agent, and the channels that
 * agent is allowed to answer in.
 *
 * It used to carry a per-channel agent and a per-person DM map, because one bot
 * could serve a whole pod. One bot is one agent now, so a channel is a place.
 */
function slack(agentName: string | null, channels: Route[] = []): AssistantSurface {
    return {
        name: 'slack',
        surface_type: 'SLACK',
        agent_name: agentName,
        uses_default_agent: agentName === null,
        config: { channels },
    } as unknown as AssistantSurface;
}

describe('surface reaches', () => {
    it('gives an agent its DMs and every channel it is allowed in', () => {
        const surface = slack('sales-agent', [
            { channel_id: 'C1', channel_name: 'sales' },
            { channel_id: 'C3', channel_name: 'deals' },
        ]);

        expect(surfaceReaches(surface, 'sales-agent').map((reach) => reach.label)).toEqual([
            'Direct messages',
            '#sales',
            '#deals',
        ]);
    });

    it('gives another agent nothing on a surface that is not theirs', () => {
        // A channel is an allow-list entry, not a route: it cannot hand one
        // channel of this bot to a different agent. That agent needs its own.
        const surface = slack('sales-agent', [
            { channel_id: 'C2', channel_name: 'support' },
        ]);

        expect(surfaceReaches(surface, 'support-agent')).toEqual([]);
        expect(surfaceAnswersDirectMessages(surface, 'support-agent')).toBe(false);
    });

    it('hands the pod default assistant the DMs when no agent claims them', () => {
        const surface = slack(null);
        expect(surfaceAnswersDirectMessages(surface, null)).toBe(true);
        expect(surfaceReaches(surface, 'sales-agent')).toEqual([]);
    });

    it('prefixes a channel name once, whether or not it arrives with one', () => {
        const surface = slack(null, [
            { channel_id: 'C1', channel_name: '#already' },
            { channel_id: 'C2', channel_name: 'bare' },
            // No name — the id is all we can show, and showing nothing would
            // silently drop a route that really does deliver messages.
            { channel_id: 'C3' },
        ]);

        expect(surfaceReaches(surface, null).map((reach) => reach.label)).toEqual([
            'Direct messages',
            '#already',
            '#bare',
            '#C3',
        ]);
    });

    it('keys channels distinctly so exploded chips stay stable', () => {
        const surface = slack(null, [
            { channel_id: 'C1', channel_name: 'a' },
            { channel_id: 'C2', channel_name: 'b' },
        ]);

        const keys = surfaceReaches(surface, null).map((reach) => reach.key);
        expect(new Set(keys).size).toBe(keys.length);
    });

    it('still describes reach as one line for the tooltip', () => {
        const surface = slack('sales-agent', [
            { channel_id: 'C1', channel_name: 'sales' },
        ]);

        expect(describeReach(surface, 'sales-agent')).toBe('Direct messages · #sales');
        expect(describeReach(surface, 'nobody')).toBe('Reaches this agent');
    });
});

/** A surface backed by someone's personal connected account. */
function connected(connection: Record<string, unknown> | null): AssistantSurface {
    return {
        name: 'telegram',
        surface_type: 'TELEGRAM',
        config: { channels: [] },
        ...(connection ? { connection: { account_id: 'a1', connector_id: 'telegram', status: 'CONNECTED', ...connection } } : {}),
    } as unknown as AssistantSurface;
}

describe('surface connection', () => {
    it('names the owner so an editor knows who to ask', () => {
        const summary = describeConnection(
            connected({
                display_name: '@acme_ops_bot',
                connected_by: { user_id: 'u1', name: 'Priya Raman', is_pod_member: true, is_you: false },
            }),
        );

        expect(summary).not.toBeNull();
        expect(summary?.label).toBe('@acme_ops_bot');
        expect(summary?.attribution).toBe('Connected by Priya Raman');
        expect(summary?.problem).toBeNull();
        expect(summary?.canRebind).toBe(false);
    });

    it('says nothing at all when the surface runs on Lemma’s own bot', () => {
        expect(describeConnection(connected(null))).toBeNull();
    });

    it('warns while it still works when the owner has left the pod', () => {
        const summary = describeConnection(
            connected({
                connected_by: { user_id: 'u1', name: 'Priya Raman', is_pod_member: false, is_you: false },
            }),
        );

        expect(summary?.problem).toBe(
            'Priya Raman has left this pod. It works until the account expires.',
        );
        expect(summary?.canRebind).toBe(true);
    });

    it('points at the owner when only they can reconnect', () => {
        const summary = describeConnection(
            connected({
                status: 'REAUTH_REQUIRED',
                connected_by: { user_id: 'u1', name: 'Priya Raman', is_pod_member: true, is_you: false },
            }),
        );

        expect(summary?.problem).toBe('Only Priya Raman can reconnect it.');
        expect(summary?.canRebind).toBe(true);
    });

    it('says nobody can reconnect it once the owner is gone', () => {
        const summary = describeConnection(
            connected({
                status: 'REAUTH_REQUIRED',
                connected_by: { user_id: 'u1', name: 'Priya Raman', is_pod_member: false, is_you: false },
            }),
        );

        expect(summary?.problem).toBe(
            'Priya Raman has left this pod, so nobody here can reconnect it.',
        );
    });

    it('addresses the owner directly when it is you', () => {
        const summary = describeConnection(
            connected({
                status: 'REAUTH_REQUIRED',
                connected_by: { user_id: 'u1', name: 'Priya Raman', is_pod_member: true, is_you: true },
            }),
        );

        expect(summary?.attribution).toBe('Connected by you');
        expect(summary?.problem).toBe('Your account needs reconnecting — nothing arrives until it does.');
    });

    it('falls back to the email when the owner has no name', () => {
        const summary = describeConnection(
            connected({
                connected_by: { user_id: 'u1', email: 'priya@acme.com', is_pod_member: true, is_you: false },
            }),
        );

        expect(summary?.attribution).toBe('Connected by priya@acme.com');
    });

    it('reports a vanished account rather than staying silent', () => {
        const summary = describeConnection(connected({ status: 'MISSING', connected_by: null }));

        expect(summary?.problem).toBe('The account this ran on no longer exists.');
        expect(summary?.canRebind).toBe(true);
    });
});
