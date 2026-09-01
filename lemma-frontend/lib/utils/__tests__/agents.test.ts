import { describe, expect, it } from 'vitest';

import {
    DEFAULT_RESPONDER_NAME,
    formatAgentName,
    isPodDefaultAgentName,
} from '@/lib/utils/agents';

describe('formatAgentName', () => {
    it('calls the pod assistant Lem, whichever name it arrives under', () => {
        // It has a real agent row now, so it reaches every list, picker and
        // header an ordinary agent does -- under the row name `pod_default`
        // from the agents endpoint and the selector `POD_DEFAULT` from the
        // schedule and conversation APIs. Humanized, those read "Pod Default"
        // and "POD DEFAULT": the job title this product deliberately dropped.
        expect(formatAgentName('pod_default')).toBe(DEFAULT_RESPONDER_NAME);
        expect(formatAgentName('POD_DEFAULT')).toBe(DEFAULT_RESPONDER_NAME);
    });

    it('leaves an ordinary agent name alone', () => {
        expect(formatAgentName('sales-agent')).toBe('Sales agent');
    });

    it('recognises the assistant by either name and nothing else', () => {
        expect(isPodDefaultAgentName('pod_default')).toBe(true);
        expect(isPodDefaultAgentName('POD_DEFAULT')).toBe(true);
        expect(isPodDefaultAgentName('pod-default')).toBe(false);
        expect(isPodDefaultAgentName(null)).toBe(false);
    });
});
