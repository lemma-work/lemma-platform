import type { AgentHostTarget, ThisComputerStatus } from '@/lib/desktop/agent-host-bridge';

export type Tone = 'ok' | 'warn' | 'muted';

export type DescribedStatus = { label: string; detail: string; tone: Tone };

const originOf = (url: string | null): string | null => {
    if (!url) return null;
    try {
        return new URL(url).origin;
    } catch {
        return null;
    }
};

/**
 * The pairing that belongs to the workspace being looked at, if any.
 *
 * A computer can be paired to several workspaces at once — that is what
 * `targets` is — but the card read `targets[0]` and every control was gated on
 * `paired`, meaning "paired to anything". Open a hosted workspace on a Mac that
 * had been paired to its own local stack and the card described the *local*
 * pairing: it reported a dead workspace as this one's status, and "Recheck
 * agents" re-polled a URL this workspace has never heard of.
 *
 * The automatic connection asks the same question and must get the same answer,
 * or a machine that is paired to something would never pair to the workspace
 * actually on screen.
 *
 * A target with no URL matches nothing: it cannot be shown to be this
 * workspace's, and guessing wrong is what this fixes.
 */
export function selectWorkspaceTarget(
    targets: readonly AgentHostTarget[],
    workspaceUrl: string | null,
): AgentHostTarget | null {
    const workspace = originOf(workspaceUrl);
    if (!workspace) return null;
    return targets.find((target) => originOf(target.url) === workspace) ?? null;
}

// What the machine can actually do right now, which is not the same question as
// whether a process is alive: an unpaired host and one that cannot reach the
// workspace are both running, and neither will pick up a run.
//
// A missing status is a state too, and used to be rendered as nothing at all.
// In a hosted workspace the first poll is the one that has to start locald, so
// "nothing yet" is the normal opening state and a failure there — no daemon, a
// build without the sidecar — left an empty space where the only way to connect
// this computer should have been.
//
// Every state here is now a report, never a prompt. There is no Connect, no
// Turn on and no Disconnect for this computer, so "not paired yet" and "not
// running yet" are both stages of an automatic connection rather than things
// waiting on the user — which is why neither says "Off". "Off" was a real state
// only because there was a switch to hold it there.
//
// Lives beside the card rather than in it so it can be tested as the pure
// function it is, like `agent-runtime-helpers` next door: the card is a client
// component and the unit suite deliberately loads no React.
export function describeThisComputer(
    status: ThisComputerStatus | null,
    error: string | null,
    workspaceUrl: string | null,
): DescribedStatus {
    if (!status) {
        return error
            ? {
                  label: 'Unavailable',
                  detail: error,
                  tone: 'warn',
              }
            : {
                  label: 'Checking',
                  detail: 'Asking this computer which agents it can run.',
                  tone: 'muted',
              };
    }
    if (!status.available) {
        return {
            label: 'Not available',
            detail: 'This build of Lemma does not include the Agent Host.',
            tone: 'muted',
        };
    }
    // Not "paired to something" — paired to the workspace on screen. A pairing
    // that belongs to another workspace is that workspace's business, and
    // reporting it here is how a dead local stack came to be this workspace's
    // status.
    const target = selectWorkspaceTarget(status.targets, workspaceUrl);
    if (!target) {
        return {
            label: 'Connecting',
            detail: 'Setting this computer up to run Claude Code, Codex, and other local agents here.',
            tone: 'muted',
        };
    }
    if (!status.running) {
        return {
            label: 'Starting',
            detail: 'Bringing this computer online for this workspace.',
            tone: 'muted',
        };
    }
    if (target?.connection_state === 'ONLINE') {
        const runs = target.active_runs ?? 0;
        return {
            label: 'Connected',
            detail: runs > 0 ? `Running ${runs} ${runs === 1 ? 'task' : 'tasks'} now.` : 'Ready for work.',
            tone: 'ok',
        };
    }
    // The same distinction the tray makes, in the same words: a failed last
    // attempt is not a reconnection in progress. A local pairing whose stack has
    // stopped never comes back on its own, and calling that "Reconnecting" for
    // days sends people to wait instead of to Disconnect.
    const failure = target?.last_error || status.last_error;
    return failure
        ? { label: 'Unreachable', detail: failure, tone: 'warn' }
        : { label: 'Reconnecting', detail: 'Trying to reach this workspace.', tone: 'warn' };
}
