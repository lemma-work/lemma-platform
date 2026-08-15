"use client";

import { useEffect, useRef } from "react";
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
export function useAutoConnectThisComputer() {
  const computer = useThisComputer();
  const { isDesktop, status, refetch } = computer;
  const createPairing = useCreateAgentHostPairing();
  // One attempt per page. A failure here is not worth a retry loop: a machine
  // that genuinely cannot pair would mint pairing codes forever, and the card
  // reports what went wrong either way.
  const attempted = useRef(false);

  useEffect(() => {
    if (!isDesktop) return;
    if (!status?.available || attempted.current) return;
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
    const pairedHere = selectWorkspaceTarget(status.targets, getLemmaApiBaseUrl()) !== null;
    if (pairedHere && status.running) return;

    attempted.current = true;
    void (async () => {
      try {
        // Only ever starts. The supervisor has no "off" to undo, so this is a
        // request to be running rather than half of a toggle.
        if (!status.running) await agentHostBridge.start();
        if (!pairedHere) {
          const pairing = await createPairing.mutateAsync({
            displayName: "This computer",
          });
          await agentHostBridge.pair(
            getLemmaApiBaseUrl(),
            pairing.pairing_code,
            "This computer",
          );
        }
        // Kick the harness scan rather than waiting for the next poll, so the
        // list has something in it by the time anyone looks.
        await agentHostBridge.refresh();
      } catch {
        // Silent by design. Nobody asked for this, so nobody should be told it
        // failed; the card says what state the computer is actually in.
      } finally {
        // locald answers on its event stream, so the reading in hand is always
        // one step behind an action.
        setTimeout(() => void refetch(), 500);
      }
    })();
  }, [createPairing, isDesktop, refetch, status]);

  return computer;
}
