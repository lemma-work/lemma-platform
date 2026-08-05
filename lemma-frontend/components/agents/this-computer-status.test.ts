import { describe, expect, it } from 'vitest';

import { describeThisComputer } from '@/components/agents/this-computer-status';
import type { ThisComputerStatus } from '@/lib/desktop/agent-host-bridge';

const status = (overrides: Partial<ThisComputerStatus> = {}): ThisComputerStatus =>
    ({
        available: true,
        running: true,
        paired: true,
        last_error: null,
        targets: [],
        ...overrides,
    }) as ThisComputerStatus;

const target = (overrides: Record<string, unknown> = {}) =>
    ({
        connection_state: 'OFFLINE',
        last_error: null,
        active_runs: null,
        ...overrides,
    }) as ThisComputerStatus['targets'][number];

describe('describeThisComputer', () => {
    it('says what it is doing before the first reading arrives', () => {
        expect(describeThisComputer(null, null).label).toBe('Checking');
    });

    it('reports a live connection and its load', () => {
        const described = describeThisComputer(
            status({ targets: [target({ connection_state: 'ONLINE', active_runs: 2 })] }),
            null,
        );
        expect(described.label).toBe('Connected');
        expect(described.detail).toBe('Running 2 tasks now.');
    });

    it('calls a failed last attempt unreachable rather than reconnecting', () => {
        // The state this exists for: a computer paired to a local workspace whose
        // stack has since stopped. It retries forever and never returns, the tray
        // says "workspace unreachable" for the same reading, and the card saying
        // "Reconnecting" sent people to wait rather than to Disconnect.
        const described = describeThisComputer(
            status({
                targets: [
                    target({
                        last_error:
                            'Agent Host HTTP request failed: error sending request for url (http://app.lemma.localhost:56608/agent-host/poll)',
                    }),
                ],
            }),
            null,
        );
        expect(described.label).toBe('Unreachable');
        expect(described.detail).toContain('app.lemma.localhost:56608');
        expect(described.tone).toBe('warn');
    });

    it('still says reconnecting while an attempt is genuinely in flight', () => {
        // Nothing recorded against the latest attempt: it is trying, and the next
        // reading may well be Connected.
        const described = describeThisComputer(status({ targets: [target()] }), null);
        expect(described.label).toBe('Reconnecting');
        expect(described.detail).toBe('Trying to reach this workspace.');
    });
});
