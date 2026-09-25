// Sharing, as far as Local settings still goes: saying who can reach this
// installation, and turning that back to This computer.
//
// Choosing a mode, the tunnel providers and who may join moved to the
// workspace's own settings (This Mac → Sharing). Stopping stays here, because
// a shared workspace moves this app's window to the shared address, where the
// workspace is deliberately not allowed to reach this computer's settings --
// so this page is the one that can always turn it off.

import { $, friendlyError, invoke, nextId, store, toast } from "./core.js";

export function modeLabel(mode) {
  if (mode === "local_network") return "Local network";
  if (mode === "public") return "Public link";
  return "This computer";
}

export function exposureCopy(mode) {
  if (mode === "local_network") return "Reachable on the selected trusted Wi-Fi interface.";
  if (mode === "public") return "Reachable from the internet through your tunnel account.";
  return "Not reachable from another device.";
}

export function renderSharingControls(sharing = {}) {
  const mode = sharing.mode || "this_computer";
  const busy = store.sharingBusy
    || Boolean(sharing.transition_running)
    || !["ready", "error"].includes(sharing.phase || "ready");
  store.sharingBusy = busy;
  const button = $("sharing-disable");
  button.hidden = mode === "this_computer";
  button.disabled = busy;
}

export async function disableSharing() {
  store.sharingBusy = true;
  renderSharingControls(store.snapshot?.sharing);
  try {
    await invoke("sharing_action", {
      action: "disable",
      id: nextId("sharing-disable"),
    });
    toast("Restoring This computer mode…");
  } catch (error) {
    store.sharingBusy = false;
    renderSharingControls(store.snapshot?.sharing);
    toast(friendlyError(error), true);
  }
}
