import { describe, expect, it } from 'vitest';

import {
    MAX_LOCAL_PART,
    buildAgentEmailPreview,
    buildLocalPart,
    slugify,
    splitEmail,
} from '@/lib/surfaces/agent-email';

/**
 * These mirror `email_address_allocation.py`. The preview is only worth showing
 * while it agrees with the allocator that actually mints the address, so the
 * cases here are the ones that module's own reasoning calls out — anything the
 * two disagree on ships as a lie in the builder.
 */

const DOMAIN = 'ops.lemma.work';

describe('the address an agent is about to get', () => {
    it('is the agent and the pod, in that order', () => {
        expect(buildAgentEmailPreview({ agentName: 'Roundtable', podName: 'Acme', domain: DOMAIN }))
            .toBe('roundtable.acme@ops.lemma.work');
    });

    it('slugifies the way the allocator does', () => {
        expect(slugify('Support Triage!')).toBe('support-triage');
        expect(slugify('  --Ops--  ')).toBe('ops');
        expect(slugify('')).toBe('agent');
        expect(slugify(null, 'pod')).toBe('pod');
    });

    // Lem is the absence of an agent, not an unset field: it is
    // the pod answering, so it gets the pod's own name and no agent half.
    it('gives Lem the pod name alone', () => {
        expect(buildAgentEmailPreview({ agentName: null, podName: 'Acme', domain: DOMAIN }))
            .toBe('acme@ops.lemma.work');
    });

    it('keeps the agent half when the budget runs out', () => {
        const agent = 'support-triage';
        const local = buildLocalPart({ agentName: agent, podName: 'x'.repeat(200) });
        expect(local.length).toBeLessThanOrEqual(MAX_LOCAL_PART);
        expect(local.startsWith(`${agent}.`)).toBe(true);
    });

    it('drops the pod half entirely for a pathologically long agent name', () => {
        const local = buildLocalPart({ agentName: 'a'.repeat(120), podName: 'Acme' });
        expect(local).toBe('a'.repeat(MAX_LOCAL_PART));
    });

    // Not a promise: an unnamed agent's address is not "agent.acme@" — that is
    // the next unnamed agent's address too.
    it('shows nothing until the agent has a name', () => {
        expect(buildAgentEmailPreview({ agentName: '', podName: 'Acme', domain: DOMAIN })).toBeNull();
        expect(buildAgentEmailPreview({ agentName: '   ', podName: 'Acme', domain: DOMAIN })).toBeNull();
        expect(buildAgentEmailPreview({ agentName: '!!!', podName: 'Acme', domain: DOMAIN })).toBeNull();
    });

    // A deployment with no mail domain mints no address, so there is nothing to
    // show — the builder must not invent one.
    it('shows nothing when this deployment mints no addresses', () => {
        expect(buildAgentEmailPreview({ agentName: 'Ops', podName: 'Acme', domain: null })).toBeNull();
        expect(buildAgentEmailPreview({ agentName: 'Ops', podName: 'Acme', domain: '  ' })).toBeNull();
    });

    it('survives a pod with no name of its own', () => {
        expect(buildAgentEmailPreview({ agentName: 'Ops', podName: null, domain: DOMAIN }))
            .toBe('ops.pod@ops.lemma.work');
    });
});

describe('splitting an address for display', () => {
    it('separates the identifying half from the shared domain', () => {
        expect(splitEmail('roundtable.acme@ops.lemma.work')).toEqual({
            local: 'roundtable.acme',
            domain: '@ops.lemma.work',
        });
    });

    it('leaves anything that is not an address alone', () => {
        expect(splitEmail('not-an-address')).toEqual({ local: 'not-an-address', domain: '' });
        expect(splitEmail('')).toBeNull();
        expect(splitEmail(null)).toBeNull();
    });
});
