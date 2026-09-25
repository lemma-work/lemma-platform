import { useEffect, useSyncExternalStore } from "react";
import { apiUrl, hasApiUrl, lemma } from "@/session/client";
import { agentHost, useAgentHost, type AgentHostStatus } from "./agent-host";
import { isDesktop } from "./bridge";
import { selectWorkspaceTarget, thisComputer } from "./this-computer";

/** Connect this computer to the workspace on screen, without being asked.
 *
 *  Pairing exists because a workspace can drive agents on machines it does not
 *  run on: name the computer, mint a code, carry it over. None of that applies
 *  to the machine you are sitting at — you are already signed in on it, and
 *  the Agent Host is a sidecar this app supervises. A "Connect this computer"
 *  button asked for consent that was already implied, and read as broken:
 *  pairing takes a moment and the agent scan longer, so pressing it looked
 *  like nothing, then nothing, then "no agents found".
 *
 *  So it happens on its own, once per workspace per page load, as soon as the
 *  authenticated shell is open in the app — hosted workspaces included, since
 *  the cloud user is the one whose laptop and workspace are genuinely apart.
 *  There is no Connect, no Turn on and no Disconnect for this computer, so
 *  nothing fights the automatic connection. Pairing grants nothing by itself:
 *  a workspace reaches an agent only through a runtime profile somebody adds on
 *  the Models page, which is the real consent step.
 *
 *  macOS may raise a file-access prompt the first time an adapter probes for an
 *  installed agent. That belongs to the agent's own binary and cannot be
 *  pre-empted; connecting early at least puts it in front of someone still in
 *  setup. */

/* ── the attempt guard ─────────────────────────────────────────────── */

/** Which workspaces this page has already tried, and why the last try failed.
 *
 *  Module-level, not per mount. Two mounts is the ordinary case — the shell
 *  and the Models card both ask — and a per-mount guard let both mint a
 *  pairing code, so one machine arrived twice and the first was orphaned
 *  offline for good. Deliberately not persisted: it guards against doing the
 *  same work twice on one page, not a decision anybody made, and a reload is a
 *  fresh start. */
const attempted = new Set<string>();
let lastFailure: string | null = null;
const failureListeners = new Set<() => void>();

function notify() {
    for (const listener of failureListeners) listener();
}

/** Claim the one attempt for this workspace, or learn someone else has. */
export function claimAttempt(workspace: string): boolean {
    if (attempted.has(workspace)) return false;
    attempted.add(workspace);
    return true;
}

function recordSuccess() {
    if (lastFailure === null) return;
    lastFailure = null;
    notify();
}

function recordFailure(cause: unknown) {
    lastFailure = cause instanceof Error ? cause.message : String(cause);
    notify();
}

export function connectFailure(): string | null {
    return lastFailure;
}

/** Let someone ask again after a failure.
 *
 *  The guard exists so a machine that cannot pair does not mint codes in a
 *  loop — not to refuse a person who pressed a button, which is why clearing
 *  it is the whole of this. */
export function retryAutoConnect() {
    attempted.clear();
    lastFailure = null;
    notify();
}

/** Test seam. */
export function resetAutoConnectForTests() {
    attempted.clear();
    lastFailure = null;
    failureListeners.clear();
}

function subscribeFailure(listener: () => void) {
    failureListeners.add(listener);
    return () => {
        failureListeners.delete(listener);
    };
}

/* ── one attempt ───────────────────────────────────────────────────── */

export interface ConnectDeps {
    /** Mint a pairing code through the session this page already has. */
    createPairing: (displayName: string) => Promise<{ pairing_code: string }>;
    host: Pick<typeof agentHost, "start" | "pair" | "refresh">;
}

/** Whether this status still needs anything from us for this workspace. */
export function needsConnecting(status: AgentHostStatus, workspace: string): boolean {
    return !(status.running && selectWorkspaceTarget(status.targets, workspace) !== null);
}

/** Connect once, if this page has not already tried for this workspace.
 *
 *  Resolves "skipped" when there was nothing to do or another caller holds the
 *  attempt, "connected" when every step answered, and "failed" — with the
 *  reason recorded for the card — when one did not. Never throws: nobody
 *  asked for this, so it must not interrupt. */
export async function connectThisComputer(
    status: AgentHostStatus,
    workspace: string,
    deps: ConnectDeps,
): Promise<"skipped" | "connected" | "failed"> {
    if (!status.available || !needsConnecting(status, workspace)) return "skipped";
    if (!claimAttempt(workspace)) return "skipped";
    const pairedHere = selectWorkspaceTarget(status.targets, workspace) !== null;
    try {
        /* Only ever starts: the supervisor has no "off" to undo. */
        if (!status.running) await deps.host.start();
        if (!pairedHere) {
            const name = pairingName();
            const pairing = await deps.createPairing(name);
            await deps.host.pair(workspace, pairing.pairing_code, name);
        }
        /* Kick the agent scan now rather than on the next poll, so the list
           has something in it by the time anyone looks. */
        await deps.host.refresh();
        recordSuccess();
        return "connected";
    } catch (cause) {
        recordFailure(cause);
        return "failed";
    }
}

/** What the computer is called in the workspace's list of computers. Seen from
 *  another machine too, so "This Mac" would be wrong there; "My Mac" is not. */
function pairingName(): string {
    const noun = thisComputer();
    return noun === "this Mac" ? "My Mac" : noun === "this PC" ? "My PC" : "My computer";
}

/** Mounted once in the authenticated shell; also returns the live status and
 *  the failure, for the card that reports them. */
export function useAutoConnectThisComputer() {
    const host = useAgentHost();
    const failure = useSyncExternalStore(subscribeFailure, connectFailure, () => null);
    const { status, refetch } = host;

    useEffect(() => {
        if (!status || !isDesktop() || !hasApiUrl()) return;
        const workspace = apiUrl();
        void connectThisComputer(status, workspace, {
            createPairing: (displayName) => lemma().agentHost.createPairing({ display_name: displayName }),
            host: agentHost,
        }).then((outcome) => {
            /* locald answers on its event stream, so the reading in hand is
               always one step behind an action. */
            if (outcome !== "skipped") setTimeout(() => void refetch(), 500);
        });
        /* `failure` is a dependency so that "Try again", which clears the
           guard and the failure together, runs this again. */
    }, [status, failure, refetch]);

    return { ...host, connectError: failure, retryConnect: retryAutoConnect };
}
