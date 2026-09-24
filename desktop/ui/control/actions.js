// The buttons that ask the shell to do something, and leaving Settings.

import {
  $,
  LOCAL_MODE,
  confirmAction,
  friendlyError,
  invoke,
  store,
  toast,
} from "./core.js";
import { loadAppUpdate, loadRuntimeInfo, postgresMajorChangeMessage } from "./updates.js";
import { retrySnapshotNow } from "./events.js";

// Nothing on this page is a draft any more -- the forms that were moved to the
// workspace's own settings -- so leaving is never a decision to make.
export async function closeLocalSettings() {
  const status = $("settings-close-status");
  status.hidden = true;
  try {
    return await leaveSettings();
  } finally {
    $("back-to-lemma").focus();
  }
}

async function leaveSettings() {
  try {
    await invoke("close_local_settings");
    return true;
  } catch (error) {
    toast(friendlyError(error), true);
    return false;
  }
}

export async function runDesktopAction(button) {
  try {
    const action = button.dataset.action;
    if (action === "restart-recovery") await invoke("restart_into_recovery");
    if (action === "start") await invoke("start");
    if (action === "restart") await invoke("restart");
    if (action === "stop") await invoke("stop", { includeInfra: false });
    if (action === "stop-all") {
      const stopEverything = await confirmAction(
        "Stop everything?",
        "Stop the Lemma application and its private runtime? Workspace data is preserved.",
        "Stop Everything",
      );
      if (!stopEverything) return;
      await invoke("stop", { includeInfra: true });
    }
    if (action === "logs") await invoke("open_logs");
    if (action === "devtools") await invoke("open_developer_tools");
    // Connecting, choosing agents and turning it off live in the workspace, so
    // a cloud user reaches the same controls. This page keeps only what is
    // useful when the workspace itself will not load.
    if (action === "agent-host-open") {
      if (!await closeLocalSettings()) return;
      await invoke("open_app");
    }
    if (action === "agent-host-restart") await invoke("agent_host_action", { action: "restart" });
    if (action === "agent-host-log") await invoke("open_logs");
    if (action === "repair-runtime") {
      const repair = await confirmAction(
        "Verify and repair the runtime?",
        "Stop Lemma briefly and verify or replace only signed runtime files?",
        "Verify & Repair",
      );
      if (!repair) return;
      document.querySelectorAll('[data-action="repair-runtime"]').forEach((item) => {
        item.disabled = true;
        item.textContent = "Repairing…";
      });
      await invoke("repair_runtime");
      await loadRuntimeInfo();
      toast("Runtime verification finished. Lemma is starting.");
    }
    // Both confirm natively inside the command rather than here: one dialog,
    // and the splash reaches the same commands without needing a dialog
    // primitive of its own.
    if (action === "check-app-update") {
      button.disabled = true;
      button.textContent = "Checking…";
      await loadAppUpdate();
    }
    if (action === "install-app-update") {
      button.disabled = true;
      button.textContent = "Downloading…";
      if (store.appUpdate?.dataCompatibility === "postgres-major-change") {
        throw new Error(postgresMajorChangeMessage(store.appUpdate));
      }
      // The version the user is looking at, so the command can refuse if the
      // feed has moved on since they were shown it.
      await invoke("install_app_update", {
        resetData: false,
        expectedVersion: store.appUpdate?.availableVersion ?? "",
      });
      await loadAppUpdate();
    }
    if (action === "retry-snapshot") {
      retrySnapshotNow();
    }
    if (action === "reset-local-data") {
      button.disabled = true;
      button.textContent = "Resetting…";
      const outcome = await invoke("reset_local_data");
      $("recovery-status").textContent = outcome === "started"
        ? "Local data reset has started. Wait for the installation to report completion."
        : "Reset cancelled. No cleanup was started.";
    }
    if (action === "full-reinstall") {
      button.disabled = true;
      button.textContent = "Starting over…";
      const outcome = await invoke("reset_full_reinstall");
      $("recovery-status").textContent = outcome === "completed"
        ? "Cleanup completed. Choose how to run Lemma to set up again."
        : "Cleanup cancelled. No cleanup was started.";
    }
  } catch (error) {
    if (["reset-local-data", "full-reinstall"].includes(button.dataset.action)) {
      $("recovery-status").textContent = friendlyError(error);
    }
    toast(friendlyError(error), true);
  } finally {
    for (const [action, label] of [
      ["reset-local-data", "Reset local data"],
      ["full-reinstall", "Force cleanup and reinstall"],
      ["check-app-update", "Check for updates"],
      ["install-app-update", "Download and install"],
    ]) {
      document.querySelectorAll(`[data-action="${action}"]`).forEach((item) => {
        item.disabled = (action === "install-app-update" && store.appUpdate?.dataCompatibility !== "compatible") || (action === "reset-local-data" && !LOCAL_MODE);
        item.textContent = label;
      });
    }
    document.querySelectorAll('[data-action="repair-runtime"]').forEach((item) => {
      item.disabled = !LOCAL_MODE || !store.runtimeInfo?.repairAvailable;
      item.textContent = "Verify & repair runtime";
    });
  }
}
