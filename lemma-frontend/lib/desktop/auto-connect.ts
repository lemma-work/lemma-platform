"use client";

import { useEffect, useSyncExternalStore } from "react";
import { agentHostBridge, useThisComputer } from "@/lib/desktop/agent-host-bridge";
import { selectWorkspaceTarget } from "@/components/agents/this-computer-status";
import { useCreateAgentHostPairing } from "@/lib/hooks/use-agent-runtime";
import { getLemmaApiBaseUrl } from "@/lib/sdk/lemma-client";

/**
 * Connect this computer to the workspace on screen, without being asked to.
 *
 * Pairing exists because a Lemma workspace can drive agents on machines it does
 * not run on — you name the computer, mint a code, and carry it over. None of
 * that applies to the machine you are sitting at: you are already signed in on
 * it, and the Agent Host is a sidecar this app supervises. Making them press
 * "Connect this computer" was asking for consent that was already implied, and
 * it read as broken, because pairing takes a moment and the harness scan takes
 * longer still — so the honest-looking outcome of pressing it was nothing, then
 * nothing, then "no agents found".
 *
 * So it happens on its own, once per page, as soon as an authenticated page is
 * open in the app. What the user is then asked is the only question actually
 * left: which of the agents we found do you want in your chats.
 *
 * This is the whole connection lifecycle. There is no Connect, no Turn on, and
 * no Disconnect for this computer, so there is nothing for an automatic
 * connection to fight with and no "did the user say no?" flag refereeing the
 * two. Removing a machine you are *not* at is a different act with a different
 * mechanism: `agent.host.revoke`, which is durable server-side because the
 * machine is not there to re-pair itself.
 *
 * Hosted workspaces get this too. The gate used to be `isLocalDeployment()`,
 * which meant the cloud user — the one who most needs an explanation of how
 * their laptop joins a workspace running somewhere else — was the only one left
 * pressing buttons.
 *
 * macOS may prompt for file access the first time an adapter probes for an
 * installed agent. That prompt belongs to the agent's own binary and cannot be
 * pre-empted from here; what this does is make sure it happens early, while the
 * user is still in setup, rather than after they press a button and conclude it
 * did nothing.
 */
/**
 * Which workspaces this page has already tried to connect to, and why the last
 * try failed.
 *
 * Module-level, not a `useRef`. The ref was per mount, and two mounts is the
 * ordinary case rather than a corner: `ThisComputerCard` and the setup banner
 * both call this hook, and the banner is on every page that has a card. Both
 * saw "not paired here", both claimed their own attempt, and both minted a
 * pairing code — so one machine arrived in the workspace twice, and because the
 * host keeps one target per workspace URL, the first of the two was orphaned
 * and sat there permanently offline.
 *
 * Deliberately not persisted. It is a guard against doing the same work twice
 * on one page, not a record of anything the user decided — there is no connect
 * or disconnect for this computer, so there is no decision to remember. A
 * reload is a fresh start, which is also what makes it safe to leave a failure
 * in it.
 */
const attemptedWorkspaces = new Set<string>();
let lastFailure: string | null = null;
const attemptListeners = new Set<() => void>();

function notifyAttempts() {
  for (const listener of attemptListeners) listener();
}

function subscribeAttempts(listener: () => void) {
  attemptListeners.add(listener);
  return () => {
    attemptListeners.delete(listener);
  };
}

function readFailure(): string | null {
  return lastFailure;
}

/** Claim the one attempt for this workspace, or report that someone else has. */
function claimAttempt(workspace: string): boolean {
  if (attemptedWorkspaces.has(workspace)) return false;
  attemptedWorkspaces.add(workspace);
  return true;
}

function recordSuccess(workspace: string) {
  if (lastFailure === null) return;
  lastFailure = null;
  attemptedWorkspaces.add(workspace);
  notifyAttempts();
}

function recordFailure(cause: unknown, workspace: string) {
  lastFailure = cause instanceof Error ? cause.message : String(cause);
  attemptedWorkspaces.add(workspace);
  notifyAttempts();
}

/**
 * Let the user ask again after a failure.
 *
 * The attempt guard exists so a machine that cannot pair does not mint pairing
 * codes in a loop. It is not a reason to refuse a person who pressed a button,
 * which is why clearing it is the whole of this.
 */
export function retryAutoConnect() {
  attemptedWorkspaces.clear();
  lastFailure = null;
  notifyAttempts();
}

/** Test seam: forget every attempt this module has recorded. */
export function resetAutoConnectForTests() {
  attemptedWorkspaces.clear();
  lastFailure = null;
  attemptListeners.clear();
}

/**
 * Test seam for the guard itself.
 *
 * Exposed because the behaviour worth pinning — one attempt per workspace,
 * shared across every mount — is not observable through the hook without
 * rendering two components and a Tauri bridge, and the unit suite deliberately
 * loads no React.
 */
export const __attemptGuardForTests = {
  claim: claimAttempt,
  succeed: recordSuccess,
  fail: recordFailure,
  failure: readFailure,
  subscribe: subscribeAttempts,
};

export function useAutoConnectThisComputer() {
  const computer = useThisComputer();
  const { isDesktop, status, refetch } = computer;
  const createPairing = useCreateAgentHostPairing();
  const failure = useSyncExternalStore(
    subscribeAttempts,
    readFailure,
    () => null as string | null,
  );

  useEffect(() => {
    if (!isDesktop) return;
    if (!status?.available) return;
    // Paired to *this* workspace, not to anything. A Mac paired to its own local
    // stack and then opened against a hosted workspace needs a second pairing,
    // and reading `status.paired` said it already had one.
    //
    // Only this machine's own answer is consulted. A revoked pairing used to
    // survive here — the host kept polling a credential Lemma had destroyed —
    // so this checked the backend's host list too and re-paired when it saw a
    // REVOKED row. The host now drops a revoked target itself, on the refusal
    // that tells it, so the target simply stops existing and the ordinary
    // "not paired here" path does the rest.
    const workspace = getLemmaApiBaseUrl();
    const pairedHere = selectWorkspaceTarget(status.targets, workspace) !== null;
    if (pairedHere && status.running) return;
    if (!claimAttempt(workspace)) return;

    void (async () => {
      try {
        // Only ever starts. The supervisor has no "off" to undo, so this is a
        // request to be running rather than half of a toggle.
        if (!status.running) await agentHostBridge.start();
        if (!pairedHere) {
          const pairing = await createPairing.mutateAsync({
            displayName: "This computer",
          });
          await agentHostBridge.pair(workspace, pairing.pairing_code, "This computer");
        }
        // Kick the harness scan rather than waiting for the next poll, so the
        // list has something in it by the time anyone looks.
        await agentHostBridge.refresh();
        recordSuccess(workspace);
      } catch (cause) {
        // Not silent any more. Nobody asked for this, so it must not interrupt
        // — but the card's only other vocabulary is "Connecting", and a
        // connection that has already failed reported itself as one still in
        // progress for as long as the page stayed open. Nothing retried,
        // because `attempted` had been set, and nothing said why.
        recordFailure(cause, workspace);
      } finally {
        // locald answers on its event stream, so the reading in hand is always
        // one step behind an action.
        setTimeout(() => void refetch(), 500);
      }
    })();
  }, [createPairing, isDesktop, refetch, status]);

  return { ...computer, connectError: failure, retryConnect: retryAutoConnect };
}
