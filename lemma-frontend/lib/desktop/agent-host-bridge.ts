'use client';

import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

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

export const agentHostBridge = {
    status: () => call('agent_host_status'),
    setEnabled: (enabled: boolean) => call('agent_host_set_enabled', { enabled }),
    pair: (url: string, pairingCode: string, name: string) =>
        call('agent_host_pair', { url, pairingCode, name }),
    unpair: (targetId?: string | null) => call('agent_host_unpair', { targetId: targetId ?? null }),
    refresh: () => call('agent_host_refresh'),
    openLog: () => call('agent_host_open_log'),
};

/**
 * Poll this computer's Agent Host while the page is visible.
 *
 * Returns null in a browser, so every caller degrades to the cloud-only view
 * without branching on the platform.
 */
export function useThisComputer(intervalMs = 3000) {
    const [status, setStatus] = useState<ThisComputerStatus | null>(null);
    const [error, setError] = useState<string | null>(null);
    // The shell injects its bridge before any page script and never removes it,
    // so there is nothing to subscribe to - but the server has no bridge at all,
    // hence the separate server snapshot rather than a render-time read.
    const isDesktop = useSyncExternalStore(
        subscribeShellBridge,
        isDesktopAgentHostAvailable,
        () => false,
    );

    const poll = useCallback(async () => {
        if (!isDesktopAgentHostAvailable()) return;
        try {
            const next = readStatus(await agentHostBridge.status());
            if (next) setStatus(next);
            setError(null);
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : String(cause));
        }
    }, []);

    useEffect(() => {
        if (!isDesktop) return;
        let timer: ReturnType<typeof setInterval> | null = null;
        // Deferred rather than called inline: the first reading arrives through
        // setState, and React forbids that synchronously inside an effect.
        const first = setTimeout(() => void poll(), 0);
        const start = () => {
            if (timer === null) timer = setInterval(() => void poll(), intervalMs);
        };
        const stop = () => {
            if (timer !== null) {
                clearInterval(timer);
                timer = null;
            }
        };
        // A background tab has nobody watching, and each poll forks the sidecar
        // to read its journal.
        const onVisibility = () => {
            if (document.hidden) {
                stop();
                return;
            }
            void poll();
            start();
        };
        document.addEventListener('visibilitychange', onVisibility);
        if (!document.hidden) start();
        return () => {
            clearTimeout(first);
            document.removeEventListener('visibilitychange', onVisibility);
            stop();
        };
    }, [intervalMs, isDesktop, poll]);

    return {
        isDesktop,
        status,
        error,
        refetch: poll,
    };
}
