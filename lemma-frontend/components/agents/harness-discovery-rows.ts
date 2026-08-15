/**
 * The coding agents Lemma knows how to drive, in the order they are shown.
 *
 * Mirrors `desktop/agent-host/agent-adapters.lock.json` — the certified adapter
 * set the Agent Host probes for. Kept here rather than fetched because it is
 * needed *before* the first probe answers: the whole point is to say which
 * agents are being looked for while the looking is still happening.
 *
 * A key that leaves the lock file simply stops matching a real harness and its
 * row stays "not installed", which is the honest answer for an agent Lemma no
 * longer drives. A key that joins it arrives from the host as a real row.
 */
export const KNOWN_HARNESSES: ReadonlyArray<{ key: string; displayName: string }> = [
    { key: 'claude-code', displayName: 'Claude Code' },
    { key: 'codex', displayName: 'Codex' },
    { key: 'opencode', displayName: 'OpenCode' },
    { key: 'cursor', displayName: 'Cursor' },
];

export type DiscoveryPhase = 'starting' | 'connecting' | 'scanning' | 'settled' | 'unavailable';

export type DiscoveredHarness = {
    harness_key: string;
    display_name: string;
    health: string;
};

export type HarnessRowState<T> =
    | { key: string; displayName: string; state: 'looking' }
    | { key: string; displayName: string; state: 'missing' }
    | { key: string; displayName: string; state: 'found'; harness: T };

/**
 * Which of the two contradictory things the panel used to say is true.
 *
 * The old empty state read "Still looking for coding agents on this Mac…" in
 * one column while the other promised "Claude Code, Codex, or OpenCode" — one
 * of them a process report and the other a menu, with nothing tying them
 * together. Both halves now come from here.
 */
export function discoveryPhase(input: {
    hostAvailable: boolean | undefined;
    paired: boolean | undefined;
    fetching: boolean;
    stillDiscovering: boolean;
}): DiscoveryPhase {
    if (input.hostAvailable === false) return 'unavailable';
    if (!input.hostAvailable) return 'starting';
    if (!input.paired) return 'connecting';
    if (input.fetching || input.stillDiscovering) return 'scanning';
    return 'settled';
}

/**
 * The known agents, each resolved to what this computer actually reported.
 *
 * Rows exist from the first frame, so the list does not appear out of nothing
 * once probing finishes — the previous version showed an empty panel with one
 * sentence in it for the whole minute a first probe takes, which reads as
 * broken rather than busy.
 *
 * A harness the host reports that is not in `KNOWN_HARNESSES` is appended
 * rather than dropped: the lock file can gain an adapter before this list does,
 * and an agent the user can see working must never be missing from the list
 * that offers it.
 */
export function harnessRowStates<T extends DiscoveredHarness>(
    detected: readonly T[],
    phase: DiscoveryPhase,
): Array<HarnessRowState<T>> {
    const byKey = new Map(detected.map((harness) => [harness.harness_key, harness]));
    const settled = phase === 'settled' || phase === 'unavailable';
    const known = KNOWN_HARNESSES.map(({ key, displayName }) => {
        const harness = byKey.get(key);
        if (harness) return { key, displayName, state: 'found' as const, harness };
        // Only once the scan is over is "not here" a fact rather than a guess.
        return { key, displayName, state: settled ? ('missing' as const) : ('looking' as const) };
    });
    const extra = detected
        .filter((harness) => !KNOWN_HARNESSES.some(({ key }) => key === harness.harness_key))
        .map((harness) => ({
            key: harness.harness_key,
            displayName: harness.display_name,
            state: 'found' as const,
            harness,
        }));
    return [...known, ...extra];
}

/** What the panel says while it resolves, in one voice. */
export function discoveryHeadline(phase: DiscoveryPhase, foundCount: number): string {
    if (phase === 'unavailable') return 'This build of Lemma cannot run local agents';
    if (phase === 'starting') return 'Starting the agent host on this Mac';
    if (phase === 'connecting') return 'Connecting this computer';
    if (phase === 'scanning') return 'Looking for coding agents on this Mac';
    if (foundCount === 0) return 'No coding agents found on this Mac';
    return foundCount === 1 ? 'Found 1 coding agent on this Mac' : `Found ${foundCount} coding agents on this Mac`;
}

export function discoveryLines(phase: DiscoveryPhase, foundCount: number): string[] {
    if (phase === 'unavailable') {
        return [
            'Connect an API provider below to get a working model.',
            'Ollama and LM Studio run on this Mac and need no key.',
        ];
    }
    if (phase !== 'settled') {
        return [
            'Each agent is started once to see what it offers.',
            'macOS may ask for file access — allow it.',
        ];
    }
    if (foundCount === 0) {
        return [
            // Installing is noticed on its own now, within seconds — detection
            // stopped meaning "spawn every agent" and became a handful of stat
            // calls against the directories already being searched.
            //
            // Rescan still earns its place, because the fingerprint watches the
            // binary and not the account: signing into an agent you already have
            // changes nothing on disk, so that is the case a human still has to
            // announce.
            'Install Claude Code, Codex, Cursor or OpenCode — it appears here on its own.',
            'Already installed one and signed in? Press Rescan.',
            'Or connect a model provider below — no agent needed.',
        ];
    }
    return [
        'A coding agent needs no API key and no model id.',
        'It runs on this Mac with its own credentials.',
        'Add one to use it in chats; you can add more later.',
    ];
}
