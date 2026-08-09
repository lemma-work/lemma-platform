import { describe, expect, it } from 'vitest';

import {
    describeConnection,
    describeReach,
    surfaceAnswersDirectMessages,
    surfaceReaches,
    surfaceReachesDefaultAgent,
} from '@/lib/utils/surfaces';
import type { AssistantSurface } from '@/lib/types';

type Route = {
    channel_id?: string;
    channel_name?: string;
    agent_name?: string | null;
    use_pod_assistant?: boolean;
};

/**
 * A Slack surface: one workspace install, a default responder, N channel
 * routes, and whatever each person picked for their own DMs.
 */
function slack(
    defaultAgent: string | null,
    channels: Route[] = [],
    dmAgentByUser: Record<string, string> = {},
): AssistantSurface {
    return {
        name: 'slack',
        surface_type: 'SLACK',
        agent_name: defaultAgent,
        uses_default_agent: defaultAgent === null,
        config: { channels, slack: { dm_agent_by_user: dmAgentByUser } },
    } as unknown as AssistantSurface;
}

describe('surface reaches', () => {
    it('lists direct messages and every channel routed to the agent', () => {
        const surface = slack('sales-agent', [
            { channel_id: 'C1', channel_name: 'sales', agent_name: 'sales-agent' },
            { channel_id: 'C2', channel_name: 'support', agent_name: 'support-agent' },
            { channel_id: 'C3', channel_name: 'deals', agent_name: 'sales-agent' },
        ]);

        expect(surfaceReaches(surface, 'sales-agent').map((reach) => reach.label)).toEqual([
            'Direct messages',
            '#sales',
            '#deals',
        ]);
    });

    it('gives an agent its channels without claiming the DMs it holds none of', () => {
        const surface = slack('sales-agent', [
            { channel_id: 'C2', channel_name: 'support', agent_name: 'support-agent' },
        ]);

        const reaches = surfaceReaches(surface, 'support-agent');
        expect(reaches.map((reach) => reach.label)).toEqual(['#support']);
        expect(surfaceAnswersDirectMessages(surface, 'support-agent')).toBe(false);
    });

    it('gives DMs to every agent someone picked, not just the default', () => {
        const surface = slack('sales-agent', [], {
            U1: 'support-agent',
            U2: 'support-agent',
            U3: '__pod_assistant__',
        });

        // The default still answers everyone who never picked...
        expect(surfaceAnswersDirectMessages(surface, 'sales-agent')).toBe(true);
        // ...and picking is what gives anyone else a reach at all.
        expect(surfaceReaches(surface, 'support-agent')[0]).toMatchObject({
            kind: 'dm',
            detail: '2 people chose this agent',
        });
        // Choosing the pod assistant is stored, so it reaches too.
        expect(surfaceAnswersDirectMessages(surface, null)).toBe(true);
    });

    it('tells an explicit pod-assistant route from one nobody has set', () => {
        const surface = slack('sales-agent', [
            { channel_id: 'C8', channel_name: 'asks', use_pod_assistant: true },
            { channel_id: 'C9', channel_name: 'general' },
        ]);

        // Explicit: the pod assistant answers, and no agent does.
        expect(surfaceReaches(surface, null).map((reach) => reach.label)).toEqual(['#asks']);
        expect(surfaceReachesDefaultAgent(surface)).toBe(true);
        // Unset: falls to the surface default, which is a *different* answer.
        expect(surfaceReaches(surface, 'sales-agent').map((reach) => reach.label)).toEqual([
            'Direct messages',
            '#general',
        ]);
    });

    it('hands the pod default assistant the DMs when no agent claims them', () => {
        const surface = slack(null);
        expect(surfaceAnswersDirectMessages(surface, null)).toBe(true);
        expect(surfaceReaches(surface, 'sales-agent')).toEqual([]);
    });

    it('prefixes a channel name once, whether or not it arrives with one', () => {
        const surface = slack(null, [
            { channel_id: 'C1', channel_name: '#already', agent_name: null },
            { channel_id: 'C2', channel_name: 'bare', agent_name: null },
            // No name — the id is all we can show, and showing nothing would
            // silently drop a route that really does deliver messages.
            { channel_id: 'C3', agent_name: null },
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
            { channel_id: 'C1', channel_name: 'a', agent_name: null },
            { channel_id: 'C2', channel_name: 'b', agent_name: null },
        ]);

        const keys = surfaceReaches(surface, null).map((reach) => reach.key);
        expect(new Set(keys).size).toBe(keys.length);
    });

    it('still describes reach as one line for the tooltip', () => {
        const surface = slack('sales-agent', [
            { channel_id: 'C1', channel_name: 'sales', agent_name: 'sales-agent' },
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
        agent_name: 'ops',
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
