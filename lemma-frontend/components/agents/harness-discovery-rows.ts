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

import { thisComputer } from '@/lib/desktop/this-computer';

export const KNOWN_HARNESSES: ReadonlyArray<{ key: string; displayName: string }> = [
    { key: 'claude-code', displayName: 'Claude Code' },
    { key: 'codex', displayName: 'Codex' },
    { key: 'opencode', displayName: 'OpenCode' },
    { key: 'cursor', displayName: 'Cursor' },
];

/**
 * How long "Rescan" stays busy after asking the host to look again.
 *
 * Rescan does not fetch anything. It asks the Agent Host to re-probe, and the
 * host reads that request off its control file on a five-second beat before it
 * starts spawning agents — so the answer arrives over the following seconds
 * through the poll this screen already runs, not at any moment worth refetching
 * at. The button stays busy for long enough to cover both, because a control
 * that finishes before anything can possibly have changed reads as a control
 * that did nothing.
 *
 * It used to wait 1.2s, which expired before the host had even read the request.
 */
export const RECHECK_SETTLE_MS = 9000;

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

/**
 * How long a first probe runs before the wait deserves an explanation.
 *
 * Under this, saying "this can take a minute" is borrowing trouble; over it,
 * saying nothing reads as a screen that has given up. The number is the point at
 * which a person starts wondering, not a measurement of the probe.
 */
export const DISCOVERY_PATIENCE_MS = 15_000;

/**
 * The one live line about what is happening, for the column the user is reading.
 *
 * All the progress copy used to live in the preview pane, which is `hidden
 * lg:flex` — so on a narrow window the screen said nothing at all while it
 * worked. This is what the left column shows beside the agents.
 *
 * `null` once there is nothing left to report: a settled screen is described by
 * its rows, and a status line that stays put after the work is done is the thing
 * that makes people distrust the next one.
 */
export function discoveryStatusLine(input: {
    phase: DiscoveryPhase;
    foundCount: number;
    elapsedMs: number;
}): string | null {
    if (input.phase === 'settled' || input.phase === 'unavailable') return null;
    if (input.phase === 'starting') return `Starting the agent host on ${thisComputer()}…`;
    if (input.phase === 'connecting') return 'Connecting this computer…';
    // Counted, not promised. Some of the four are simply not installed and will
    // never report, so "2 of 4" would be a progress bar that stops at 2 and
    // reads as stuck.
    const found =
        input.foundCount === 0
            ? 'Looking for coding agents'
            : `Found ${input.foundCount} so far, still looking`;
    return input.elapsedMs >= DISCOVERY_PATIENCE_MS
        ? `${found} — each one is started once, which can take a minute the first time`
        : `${found}…`;
}

/** What the panel says while it resolves, in one voice. */
export function discoveryHeadline(phase: DiscoveryPhase, foundCount: number): string {
    if (phase === 'unavailable') return 'This build of Lemma cannot run local agents';
    if (phase === 'starting') return `Starting the agent host on ${thisComputer()}`;
    if (phase === 'connecting') return 'Connecting this computer';
    if (phase === 'scanning') return `Looking for coding agents on ${thisComputer()}`;
    if (foundCount === 0) return `No coding agents found on ${thisComputer()}`;
    return foundCount === 1
        ? `Found 1 coding agent on ${thisComputer()}`
        : `Found ${foundCount} coding agents on ${thisComputer()}`;
}

export function discoveryLines(phase: DiscoveryPhase, foundCount: number): string[] {
    if (phase === 'unavailable') {
        return [
            'Connect an API provider below to get a working model.',
            `Ollama and LM Studio run on ${thisComputer()} and need no key.`,
        ];
    }
    if (phase !== 'settled') {
        return [
            'Each agent is started once to see what it offers.',
            // Two lines, always. This used to drop the second one off macOS,
            // which changes the array's *length* between the server render and
            // the first client one -- and React repairs a structural mismatch
            // by discarding the server subtree, not by patching the text. The
            // sentence is true everywhere; only macOS is loud about it.
            'Your system may ask for file access — allow it.',
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
        `It runs on ${thisComputer()} with its own credentials.`,
        'Add one to use it in chats; you can add more later.',
    ];
}
