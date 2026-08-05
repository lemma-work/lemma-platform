import { describe, expect, it } from 'vitest';

import {
    describeThisComputer,
    selectWorkspaceTarget,
} from '@/components/agents/this-computer-status';
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

const WORKSPACE = 'https://asur.work';

const target = (overrides: Record<string, unknown> = {}) =>
    ({
        url: WORKSPACE,
        connection_state: 'OFFLINE',
        last_error: null,
        active_runs: null,
        ...overrides,
    }) as ThisComputerStatus['targets'][number];

describe('describeThisComputer', () => {
    it('says what it is doing before the first reading arrives', () => {
        expect(describeThisComputer(null, null, WORKSPACE).label).toBe('Checking');
    });

    it('reports a live connection and its load', () => {
        const described = describeThisComputer(
            status({ targets: [target({ connection_state: 'ONLINE', active_runs: 2 })] }),
            null,
            WORKSPACE,
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
            WORKSPACE,
        );
        expect(described.label).toBe('Unreachable');
        expect(described.detail).toContain('app.lemma.localhost:56608');
        expect(described.tone).toBe('warn');
    });

    it('still says reconnecting while an attempt is genuinely in flight', () => {
        // Nothing recorded against the latest attempt: it is trying, and the next
        // reading may well be Connected.
        const described = describeThisComputer(status({ targets: [target()] }), null, WORKSPACE);
        expect(described.label).toBe('Reconnecting');
        expect(described.detail).toBe('Trying to reach this workspace.');
    });

    it('ignores a pairing that belongs to another workspace', () => {
        // The failure this came from: a Mac paired to its own local stack, then
        // opened against a hosted workspace. The local pairing is real and is
        // failing, but it is not this workspace's, so this workspace is simply
        // not connected — and the card must offer to connect it rather than
        // reporting someone else's dead URL and hiding the button.
        const described = describeThisComputer(
            status({
                targets: [
                    target({
                        url: 'http://app.lemma.localhost:56608',
                        last_error: 'error sending request for url',
                    }),
                ],
            }),
            null,
            WORKSPACE,
        );
        expect(described.label).toBe('Not connected');
        expect(described.detail).toContain('Connect this computer');
    });
});

describe('selectWorkspaceTarget', () => {
    it('picks the pairing for this workspace out of several', () => {
        const mine = target({ url: WORKSPACE, target_id: 'mine' });
        const theirs = target({ url: 'http://app.lemma.localhost:56608', target_id: 'theirs' });
        expect(selectWorkspaceTarget([theirs, mine], WORKSPACE)?.target_id).toBe('mine');
    });

    it('matches on origin, so a path or trailing slash still pairs up', () => {
        expect(selectWorkspaceTarget([target({ url: 'https://asur.work/' })], WORKSPACE)).not.toBeNull();
    });

    it('never matches a target with no url, rather than guessing it is ours', () => {
        expect(selectWorkspaceTarget([target({ url: null })], WORKSPACE)).toBeNull();
    });

    it('has no answer when the workspace url is unreadable', () => {
        expect(selectWorkspaceTarget([target()], 'not a url')).toBeNull();
    });
});
