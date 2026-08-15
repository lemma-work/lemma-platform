'use client';

import { useSyncExternalStore } from 'react';

// The desktop shell's view of the Agent Host on *this* machine.
//
// Everything else about a paired computer comes from the backend, which only
// knows what the host last reported over the network - so it can say a computer
// is offline, but never why, and it cannot turn anything on. These are the two
// questions only the machine itself can answer: is the process running, and is
// it actually reaching the workspace.
export type AgentHostTarget = {
    target_id: string | null;
    host_id: string | null;
    name: string | null;
    url: string | null;
    enabled: boolean | null;
    connection_state: 'ONLINE' | 'OFFLINE' | null;
    last_connected_at: string | null;
    last_error: string | null;
    active_runs: number | null;
    pending_events: number | null;
};

export type ThisComputerStatus = {
    available: boolean;
    running: boolean;
    desired_running: boolean;
    paired: boolean;
    targets: AgentHostTarget[];
    uptime_seconds: number | null;
    last_error: string | null;
    log: string | null;
};

type TauriInvoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

function subscribeShellBridge() {
    return () => {};
}

function invoker(): TauriInvoke | null {
    if (typeof window === 'undefined') return null;
    const invoke = window.__TAURI__?.core?.invoke;
    return typeof invoke === 'function' ? invoke : null;
}

/** Whether this page is running inside the desktop shell at all. */
export function isDesktopAgentHostAvailable(): boolean {
    return invoker() !== null && Boolean(window.__LEMMA_DESKTOP__);
}

/**
 * The same question without the polling.
 *
 * A page that only needs to know "app or browser?" — to choose between showing
 * this machine and offering the download — should not also start a 3s poll that
 * forks the sidecar to read its journal.
 */
export function useIsDesktopShell(): boolean {
    return useSyncExternalStore(subscribeShellBridge, isDesktopAgentHostAvailable, () => false);
}

// locald answers the shell on its event stream, not as a return value, so each
// call asks for a fresh reading and returns the newest one already in hand. A
// poll is therefore up to one interval behind, which is why the card triggers
// an immediate re-poll after any action rather than trusting the first answer.
export function readStatus(payload: unknown): ThisComputerStatus | null {
    if (!payload || typeof payload !== 'object') return null;
    const record = payload as Record<string, unknown>;
    if (typeof record.available !== 'boolean') return null;
    const targets = Array.isArray(record.targets) ? (record.targets as AgentHostTarget[]) : [];
    return {
        available: record.available,
        running: record.running === true,
        desired_running: record.desired_running === true,
        paired: record.paired === true,
        targets,
        uptime_seconds: typeof record.uptime_seconds === 'number' ? record.uptime_seconds : null,
        last_error: typeof record.last_error === 'string' ? record.last_error : null,
        log: typeof record.log === 'string' ? record.log : null,
    };
}

async function call(command: string, args?: Record<string, unknown>): Promise<unknown> {
    const invoke = invoker();
    if (!invoke) throw new Error('Local settings is only available in the Lemma desktop app');
    return invoke(command, args);
}

// No `setEnabled` and no `unpair`. Turning this computer off was a preference
// that had to be remembered, reconciled against an automatic connection, and
// reported as a state of its own; unpairing the machine you are sitting at was
// undone by the next page load. Both are gone, so the bridge can only ask this
// computer to be running and to look again — never to stop, and never to forget
// a workspace. Removing a computer is `agent.host.revoke` on the backend, which
// is the only "no" that has anywhere durable to live.
export const agentHostBridge = {
    status: () => call('agent_host_status'),
    start: () => call('agent_host_start'),
    pair: (url: string, pairingCode: string, name: string) =>
        call('agent_host_pair', { url, pairingCode, name }),
    refresh: () => call('agent_host_refresh'),
    openLog: () => call('agent_host_open_log'),
};

/** The longest a run of failing status calls backs off to. */
const MAX_BACKOFF_INTERVAL_MS = 60_000;
const STATUS_INTERVAL_MS = 3000;

/**
 * One poll for the page, however many components ask.
 *
 * This used to be `useState` inside the hook, so every mount ran its own three
 * second timer — and each tick forks the sidecar to read its journal. Two
 * mounts is the normal case, not a corner: `ThisComputerCard` and the setup
 * banner both want this computer's status, and the banner is on every page
 * that has one.
 *
 * Sharing it also removes a race rather than only a cost. The mounts each held
 * their own idea of whether this machine was paired yet, so
 * `useAutoConnectThisComputer` could see "not paired" twice and pair twice,
 * leaving two computers in the workspace for one machine.
 */
type ThisComputerSnapshot = {
    status: ThisComputerStatus | null;
    error: string | null;
};

const SERVER_SNAPSHOT: ThisComputerSnapshot = { status: null, error: null };

// Replaced rather than mutated: `useSyncExternalStore` compares snapshots by
// identity and re-renders every subscriber whenever it changes, so a new object
// per poll would re-render everything three times a second forever.
let currentSnapshot: ThisComputerSnapshot = SERVER_SNAPSHOT;
const statusListeners = new Set<() => void>();
let statusTimer: ReturnType<typeof setTimeout> | null = null;
let consecutiveFailures = 0;

function publish(next: ThisComputerSnapshot) {
    if (
        next.error === currentSnapshot.error
        && sameStatus(next.status, currentSnapshot.status)
    ) {
        return;
    }
    currentSnapshot = next;
    for (const listener of statusListeners) listener();
}

// The shell answers with a fresh object every time, and almost every answer is
// identical to the last. Comparing the serialized form is not elegant, but the
// payload is small, flat and already JSON on the wire.
function sameStatus(a: ThisComputerStatus | null, b: ThisComputerStatus | null): boolean {
    if (a === b) return true;
    if (!a || !b) return false;
    return JSON.stringify(a) === JSON.stringify(b);
}

/** Ask the shell now, outside the schedule. Resolves false if the call failed. */
export async function refetchThisComputer(): Promise<boolean> {
    if (!isDesktopAgentHostAvailable()) return true;
    try {
        const next = readStatus(await agentHostBridge.status());
        publish({ status: next ?? currentSnapshot.status, error: null });
        return true;
    } catch (cause) {
        publish({
            status: currentSnapshot.status,
            error: cause instanceof Error ? cause.message : String(cause),
        });
        return false;
    }
}

// A failing call is not a slow one: an ACL rejection, a missing sidecar, or a
// daemon that will not start answers immediately and answers the same way every
// time. At a flat interval that is twenty forked sidecar reads a minute
// producing one unchanged error, for as long as the page is open, so each
// consecutive failure doubles the wait. One success puts it straight back on
// the interval it was asked for.
function nextDelay(): number {
    return consecutiveFailures === 0
        ? STATUS_INTERVAL_MS
        : Math.min(STATUS_INTERVAL_MS * 2 ** consecutiveFailures, MAX_BACKOFF_INTERVAL_MS);
}

function stopPolling() {
    if (statusTimer !== null) {
        clearTimeout(statusTimer);
        statusTimer = null;
    }
}

function startPolling() {
    if (statusTimer !== null || statusListeners.size === 0) return;
    const tick = async () => {
        const ok = await refetchThisComputer();
        if (statusTimer === null && statusListeners.size === 0) return;
        consecutiveFailures = ok ? 0 : consecutiveFailures + 1;
        statusTimer = setTimeout(() => void tick(), nextDelay());
    };
    statusTimer = setTimeout(() => void tick(), 0);
}

// A background tab has nobody watching, and each poll forks the sidecar.
function onVisibilityChange() {
    stopPolling();
    if (document.hidden) return;
    // Coming back is a reason to try again now: whatever was failing may have
    // been fixed in the meantime, and the person is looking at it.
    consecutiveFailures = 0;
    startPolling();
}

function subscribeStatus(listener: () => void) {
    statusListeners.add(listener);
    if (statusListeners.size === 1 && isDesktopAgentHostAvailable()) {
        document.addEventListener('visibilitychange', onVisibilityChange);
        if (!document.hidden) startPolling();
    }
    return () => {
        statusListeners.delete(listener);
        if (statusListeners.size === 0) {
            document.removeEventListener('visibilitychange', onVisibilityChange);
            stopPolling();
        }
    };
}

function getStatusSnapshot(): ThisComputerSnapshot {
    return currentSnapshot;
}

function getServerStatusSnapshot(): ThisComputerSnapshot {
    return SERVER_SNAPSHOT;
}

/**
 * Poll this computer's Agent Host while the page is visible.
 *
 * Returns null in a browser, so every caller degrades to the cloud-only view
 * without branching on the platform.
 */
export function useThisComputer() {
    // The shell injects its bridge before any page script and never removes it,
    // so there is nothing to subscribe to - but the server has no bridge at all,
    // hence the separate server snapshot rather than a render-time read.
    const isDesktop = useSyncExternalStore(
        subscribeShellBridge,
        isDesktopAgentHostAvailable,
        () => false,
    );
    const { status, error } = useSyncExternalStore(
        subscribeStatus,
        getStatusSnapshot,
        getServerStatusSnapshot,
    );

    return {
        isDesktop,
        status,
        error,
        refetch: refetchThisComputer,
    };
}

/** Test seam: forget everything one poller learned. */
export function resetThisComputerForTests() {
    stopPolling();
    statusListeners.clear();
    consecutiveFailures = 0;
    currentSnapshot = SERVER_SNAPSHOT;
}
