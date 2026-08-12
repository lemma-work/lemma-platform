import { describe, expect, it } from 'vitest';

import { adoptableLocalAgent } from '@/lib/desktop/local-agent-default';

/**
 * A local install's first pod has to open with a model that answers. Setting one
 * up in onboarding and then creating a pod that has none is the failure this
 * picks the agent for — the pod's first message died on "No LLM model is
 * configured on this server", with the agent sitting right there unused.
 */
const agent = (over: Record<string, unknown> = {}) => ({
    id: 'agent-1',
    name: 'Claude Code',
    kind: 'HARNESS',
    status: 'ACTIVE',
    created_at: '2026-08-12T04:31:40Z',
    ...over,
});

describe('adoptableLocalAgent', () => {
    it('adopts the coding agent onboarding just configured', () => {
        expect(adoptableLocalAgent([agent()])?.id).toBe('agent-1');
    });

    it('takes the one set up first when there are several', () => {
        // Stable across refetches: the listing's order is the server's, and two
        // runs of onboarding must not adopt different agents from one list.
        const picked = adoptableLocalAgent([
            agent({ id: 'later', created_at: '2026-08-12T09:00:00Z' }),
            agent({ id: 'first', created_at: '2026-08-12T04:00:00Z' }),
        ]);

        expect(picked?.id).toBe('first');
    });

    it('ignores a provider profile, which answers as the system default anyway', () => {
        expect(adoptableLocalAgent([agent({ kind: 'PROVIDER' })])).toBeNull();
    });

    it('ignores an archived agent, which cannot be selected for a run', () => {
        expect(adoptableLocalAgent([agent({ status: 'ARCHIVED' })])).toBeNull();
    });

    it('adopts nothing when setup was deferred', () => {
        expect(adoptableLocalAgent([])).toBeNull();
        expect(adoptableLocalAgent(undefined)).toBeNull();
    });
});
