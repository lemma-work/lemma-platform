import { describe, expect, it } from 'vitest';

import {
    KNOWN_HARNESSES,
    discoveryHeadline,
    discoveryLines,
    discoveryPhase,
    harnessRowStates,
    type DiscoveredHarness,
} from './harness-discovery-rows';

const harness = (key: string, health = 'READY'): DiscoveredHarness & { id: string } => ({
    id: `id-${key}`,
    harness_key: key,
    display_name: key,
    health,
});

describe('discoveryPhase', () => {
    // The panel used to answer this question twice, differently: an empty state
    // that said "Still looking for coding agents on this Mac…" and, beside it, a
    // preview promising "Claude Code, Codex, or OpenCode" as though they were
    // already found. One reading, so the two halves cannot disagree.
    it('reports the host starting before it reports connecting', () => {
        expect(
            discoveryPhase({
                hostAvailable: undefined,
                paired: false,
                fetching: false,
                stillDiscovering: false,
            }),
        ).toBe('starting');
    });

    it('separates an unavailable build from a host that has not answered', () => {
        // "This build has no Agent Host" is permanent; "no answer yet" is a
        // moment. Showing the same sentence for both sent people to look for a
        // problem that did not exist.
        expect(
            discoveryPhase({
                hostAvailable: false,
                paired: false,
                fetching: false,
                stillDiscovering: false,
            }),
        ).toBe('unavailable');
    });

    it('stays scanning while the host may still be installing adapters', () => {
        // A first pairing does not probe agents, it installs them, and that runs
        // for minutes. Calling it settled would say "none found" about a machine
        // that has not looked yet.
        expect(
            discoveryPhase({
                hostAvailable: true,
                paired: true,
                fetching: false,
                stillDiscovering: true,
            }),
        ).toBe('scanning');
    });

    it('settles only when nothing is outstanding', () => {
        expect(
            discoveryPhase({
                hostAvailable: true,
                paired: true,
                fetching: false,
                stillDiscovering: false,
            }),
        ).toBe('settled');
    });
});

describe('harnessRowStates', () => {
    it('lists every known agent from the first frame', () => {
        const rows = harnessRowStates([], 'scanning');

        expect(rows).toHaveLength(KNOWN_HARNESSES.length);
        expect(rows.every((row) => row.state === 'looking')).toBe(true);
    });

    it('only calls an agent missing once the scan is over', () => {
        // The distinction the whole module exists for: "not here yet" and "not
        // installed" look identical in an empty list and mean opposite things.
        expect(harnessRowStates([], 'scanning')[0].state).toBe('looking');
        expect(harnessRowStates([], 'settled')[0].state).toBe('missing');
    });

    it('resolves each agent independently', () => {
        const rows = harnessRowStates([harness('claude-code')], 'scanning');
        const byKey = new Map(rows.map((row) => [row.key, row.state]));

        expect(byKey.get('claude-code')).toBe('found');
        expect(byKey.get('codex')).toBe('looking');
    });

    it('keeps a signed-out agent as found, so the row can say why', () => {
        // AUTH_REQUIRED is a *found* agent with a problem, and the row is what
        // says so. Filtering it out here would put us back where we started:
        // an agent the user can see installed that Lemma insists is absent.
        const rows = harnessRowStates([harness('codex', 'AUTH_REQUIRED')], 'settled');

        expect(rows.find((row) => row.key === 'codex')?.state).toBe('found');
    });

    it('never drops an agent this computer reported but we have not heard of', () => {
        // The adapter lock file can gain an entry before this list does. An
        // agent the user can watch working must not be missing from the list
        // that offers it.
        const rows = harnessRowStates([harness('some-new-agent')], 'settled');

        expect(rows.find((row) => row.key === 'some-new-agent')?.state).toBe('found');
        expect(rows).toHaveLength(KNOWN_HARNESSES.length + 1);
    });
});

describe('what the panel says', () => {
    it('never promises agents while it is still looking', () => {
        // The exact contradiction from the screenshot: a preview naming three
        // agents next to a column admitting it had not found any.
        const lines = discoveryLines('scanning', 0).join(' ');

        expect(discoveryHeadline('scanning', 0)).toContain('Looking');
        expect(lines).not.toContain('Claude Code');
    });

    it('counts what it found once it is done', () => {
        expect(discoveryHeadline('settled', 1)).toContain('1 coding agent');
        expect(discoveryHeadline('settled', 3)).toContain('3 coding agents');
        expect(discoveryHeadline('settled', 0)).toContain('No coding agents');
    });

    it('offers the way out when nothing was found', () => {
        expect(discoveryLines('settled', 0).join(' ')).toContain('Rescan');
    });
});
