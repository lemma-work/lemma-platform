"use client";

import { useEffect, useRef } from "react";
import { agentHostBridge, useThisComputer } from "@/lib/desktop/agent-host-bridge";
import { useCreateAgentHostPairing } from "@/lib/hooks/use-agent-runtime";
import { getLemmaApiBaseUrl } from "@/lib/sdk/lemma-client";
import { isLocalDeployment } from "@/lib/config";

/**
 * Connect this computer to its own workspace, without being asked to.
 *
 * Pairing exists because a Lemma workspace can drive agents on machines it does
 * not run on — you name the computer, mint a code, and carry it over. None of
 * that applies to a local install: the workspace *is* this machine, the user is
 * already signed in on it, and the Agent Host is a sidecar the app itself
 * supervises. Making them press "Connect this computer" was asking for consent
 * that was already implied, and it read as broken, because pairing takes a
 * moment and the harness scan takes longer still — so the honest-looking
 * outcome of pressing it was nothing, then nothing, then "no agents found".
 *
 * So it happens on its own, once, as soon as an authenticated local page is
 * open. What the user is then asked is the only question actually left: which
 * of the agents we found do you want in your chats.
 *
 * macOS may prompt for file access the first time an adapter probes for an
 * installed agent. That prompt belongs to the agent's own binary and cannot be
 * pre-empted from here; what this does is make sure it happens early, while the
 * user is still in setup, rather than after they press a button and conclude it
 * did nothing.
 *
 * It stops at the first sign the user disagrees. Disconnecting this computer or
 * turning the Agent Host off is a decision, and silently undoing it on the next
 * render is worse than never having automated the connection at all — the user
 * presses Disconnect, watches it reconnect, and reasonably concludes the button
 * is broken.
 */

/**
 * Set when the user disconnects or turns the host off from this workspace.
 *
 * Deliberately `localStorage`: it has to outlive the page, because the whole
 * failure mode is a reconnect on the next navigation. Cleared the moment they
 * connect again by hand, which is them changing their mind.
 */
const DECLINED_KEY = "lemma.desktop.auto-connect-declined";

export function declineAutoConnect() {
    try {
        window.localStorage.setItem(DECLINED_KEY, "1");
    } catch {
        // Private mode or a blocked store. Losing the preference means the
        // computer reconnects on the next page, which is the old behaviour
        // rather than a new failure.
    }
}

export function allowAutoConnect() {
    try {
        window.localStorage.removeItem(DECLINED_KEY);
    } catch {
        // As above.
    }
}

function autoConnectDeclined(): boolean {
    try {
        return window.localStorage.getItem(DECLINED_KEY) === "1";
    } catch {
        return false;
    }
}

export function useAutoConnectThisComputer() {
  const { isDesktop, status, refetch } = useThisComputer();
  const createPairing = useCreateAgentHostPairing();
  // One attempt per page. A failure here is not worth a retry loop: the user
  // can still connect by hand from Models, and a loop against a machine that
  // genuinely cannot pair would mint pairing codes forever.
  const attempted = useRef(false);

  useEffect(() => {
    if (!isDesktop || !isLocalDeployment()) return;
    if (!status?.available || attempted.current) return;
    if (status.paired && status.running) return;
    // They already said no. Respect it until they say otherwise.
    if (autoConnectDeclined()) return;

    attempted.current = true;
    void (async () => {
      try {
        if (!status.running) await agentHostBridge.setEnabled(true);
        if (!status.paired) {
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
        // failed; the manual path in Models still says what is wrong.
      } finally {
        // locald answers on its event stream, so the reading in hand is always
        // one step behind an action.
        setTimeout(() => void refetch(), 500);
      }
    })();
  }, [createPairing, isDesktop, refetch, status]);

  return status;
}
