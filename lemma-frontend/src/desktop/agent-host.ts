import { useSyncExternalStore } from "react";
import { invoke, isDesktop } from "./bridge";

/** The desktop shell's view of the Agent Host on *this* machine.
 *
 *  Everything else about a paired computer comes from the backend, which knows
 *  only what the host last reported over the network — so it can say a
 *  computer is offline, but never why, and it cannot turn anything on. These
 *  are the two questions only the machine itself can answer: is the process
 *  running, and is it actually reaching the workspace. `targets[].host_id` is
 *  the id `/me/runtime/agent-hosts` returns, which is how the Models page
 *  recognises which listed computer is this one. */
export interface AgentHostTarget {
    target_id: string | null;
    host_id: string | null;
    /** Whose pairing this is. A pairing of somebody other than the person
     *  signed in is not this workspace's, here. Absent from an older shell. */
    user_id?: string | null;
    /** The pairing with the Lemma installed on this computer. */
    local?: boolean | null;
    /** Paused because somebody else, or nobody, is signed in to the app. */
    session_paused?: boolean | null;
    name: string | null;
    url: string | null;
    enabled: boolean | null;
    connection_state: "ONLINE" | "OFFLINE" | null;
    last_connected_at: string | null;
    last_error: string | null;
    active_runs: number | null;
    pending_events: number | null;
}

export interface AgentHostStatus {
    available: boolean;
    running: boolean;
    desired_running: boolean;
    paired: boolean;
    targets: AgentHostTarget[];
    uptime_seconds: number | null;
    last_error: string | null;
    log: string | null;
    /** The host kept exiting and locald stopped restarting it. Start clears it. */
    restart_circuit_open: boolean;
    /** "Run commands on this Mac": whether the owner turned it on, and
     *  whether this computer can confine commands at all (macOS only). Null
     *  from a shell too old to say. */
    host_execution: { enabled: boolean; available: boolean } | null;
}

/** Narrow the shell's loose JSON to a status, or null if it is not one. */
export function readStatus(payload: unknown): AgentHostStatus | null {
    if (!payload || typeof payload !== "object") return null;
    const record = payload as Record<string, unknown>;
    if (typeof record.available !== "boolean") return null;
    return {
        available: record.available,
        running: record.running === true,
        desired_running: record.desired_running === true,
        paired: record.paired === true,
        targets: Array.isArray(record.targets) ? (record.targets as AgentHostTarget[]) : [],
        uptime_seconds: typeof record.uptime_seconds === "number" ? record.uptime_seconds : null,
        last_error: typeof record.last_error === "string" ? record.last_error : null,
        log: typeof record.log === "string" ? record.log : null,
        restart_circuit_open: record.restart_circuit_open === true,
        host_execution: readHostExecution(record.host_execution),
    };
}

function readHostExecution(raw: unknown): AgentHostStatus["host_execution"] {
    if (!raw || typeof raw !== "object") return null;
    const record = raw as Record<string, unknown>;
    return { enabled: record.enabled === true, available: record.available === true };
}

/** What this page may ask of this computer's Agent Host.
 *
 *  No stop and no unpair. Turning this computer off was a preference that had
 *  to be remembered and reconciled against an automatic connection; unpairing
 *  the machine you are sitting at was undone by the next page load. So the
 *  workspace can ask it to be running, to pair, and to look again — never the
 *  reverse. Removing a computer is `agent.host.revoke` on the backend, the only
 *  "no" with somewhere durable to live. `workspace.json` grants exactly these.
 *
 *  locald answers the shell on its event stream, not as a return value, so a
 *  status read returns the newest reading already in hand — up to one poll
 *  behind any action, which is why callers re-poll after acting. */
export const agentHost = {
    status: () => invoke("agent_host_status"),
    start: () => invoke("agent_host_start"),
    /** `url` is only checked by the shell, never trusted: it pairs with the
     *  Lemma it itself navigated to. `reenable` only from a person's click,
     *  after this computer was removed from their account. */
    pair: (url: string, pairingCode: string, name: string, reenable = false) =>
        invoke("agent_host_pair", { url, pairingCode, name, reenable }),
    /** Who is signed in to the workspace on screen; `null` once they signed
     *  out. Anybody else's pairing takes no new work meanwhile. */
    session: (url: string, userId: string | null) => invoke("agent_host_session", { url, userId }),
    refresh: () => invoke("agent_host_refresh"),
    openLog: () => invoke("agent_host_open_log"),
};

/* ── one poll for the page ─────────────────────────────────────────── */

/** The status, and why the shell would not give one.
 *
 *  One poll however many components ask. Each tick forks the sidecar to read
 *  its journal, and a per-mount poll did that once per mount — and, worse, let
 *  two mounts each decide "not paired yet" and pair twice, leaving two
 *  computers in the workspace for one machine. */
export interface AgentHostSnapshot {
    status: AgentHostStatus | null;
    error: string | null;
}

const SERVER_SNAPSHOT: AgentHostSnapshot = { status: null, error: null };
const STATUS_INTERVAL_MS = 3_000;
const MAX_BACKOFF_MS = 60_000;

/* Replaced rather than mutated: `useSyncExternalStore` compares by identity,
   so a fresh object per identical poll would re-render every subscriber every
   three seconds for ever. */
let current: AgentHostSnapshot = SERVER_SNAPSHOT;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;
let failures = 0;

function publish(next: AgentHostSnapshot) {
    if (next.error === current.error && JSON.stringify(next.status) === JSON.stringify(current.status)) return;
    current = next;
    for (const listener of listeners) listener();
}

/** Ask the shell now, outside the schedule. Resolves false if it would not answer. */
export async function refetchAgentHost(): Promise<boolean> {
    if (!isDesktop()) return true;
    try {
        const next = readStatus(await agentHost.status());
        publish({ status: next ?? current.status, error: null });
        return true;
    } catch (cause) {
        publish({ status: current.status, error: cause instanceof Error ? cause.message : String(cause) });
        return false;
    }
}

/** A failing call is not a slow one: an ACL refusal or a missing sidecar
 *  answers at once, the same way every time. At a flat interval that is twenty
 *  forked sidecar reads a minute producing one unchanged error, so each
 *  consecutive failure doubles the wait, and one success resets it. */
export function nextDelay(consecutiveFailures: number): number {
    return consecutiveFailures === 0
        ? STATUS_INTERVAL_MS
        : Math.min(STATUS_INTERVAL_MS * 2 ** consecutiveFailures, MAX_BACKOFF_MS);
}

function stopPolling() {
    if (timer !== null) {
        clearTimeout(timer);
        timer = null;
    }
}

function startPolling() {
    if (timer !== null || listeners.size === 0) return;
    const tick = async () => {
        const ok = await refetchAgentHost();
        if (timer === null && listeners.size === 0) return;
        failures = ok ? 0 : failures + 1;
        timer = setTimeout(() => void tick(), nextDelay(failures));
    };
    timer = setTimeout(() => void tick(), 0);
}

/* A background tab has nobody watching, and each poll forks the sidecar.
   Coming back is a reason to try again now: whatever was failing may have been
   fixed in the meantime, and somebody is looking at it. */
function onVisibilityChange() {
    stopPolling();
    if (document.hidden) return;
    failures = 0;
    startPolling();
}

function subscribe(listener: () => void) {
    listeners.add(listener);
    if (listeners.size === 1 && isDesktop()) {
        document.addEventListener("visibilitychange", onVisibilityChange);
        if (!document.hidden) startPolling();
    }
    return () => {
        listeners.delete(listener);
        if (listeners.size === 0) {
            document.removeEventListener("visibilitychange", onVisibilityChange);
            stopPolling();
        }
    };
}

/** This computer's Agent Host, polled while the page is visible. The status is
 *  null in a browser, so callers degrade to the cloud-only view without
 *  branching on the platform. */
export function useAgentHost(): AgentHostSnapshot & { refetch: () => Promise<boolean> } {
    const snapshot = useSyncExternalStore(subscribe, () => current, () => SERVER_SNAPSHOT);
    return { ...snapshot, refetch: refetchAgentHost };
}

/** Test seam: forget everything the poller learned. */
export function resetAgentHostForTests() {
    stopPolling();
    listeners.clear();
    failures = 0;
    current = SERVER_SNAPSHOT;
}
