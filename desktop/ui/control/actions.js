// The buttons that ask the shell to do something, and leaving Settings.

import {
  $,
  LOCAL_MODE,
  confirmAction,
  friendlyError,
  invoke,
  nextId,
  pendingSaves,
  store,
  toast,
} from "./core.js";
import { loadAppUpdate, loadRuntimeInfo, postgresMajorChangeMessage } from "./updates.js";
import { saveConfiguration } from "./config.js";
import { renderSandboxImage } from "./overview.js";
import { retrySnapshotNow } from "./events.js";

let closeDecisionPending = false;

export async function closeLocalSettings() {
  if (closeDecisionPending) return false;
  const status = $("settings-close-status");
  status.hidden = true;
  if (pendingSaves.size) {
    status.textContent = "A save is still running. Wait for its result before closing.";
    status.hidden = false;
    return false;
  }
  closeDecisionPending = true;
  try {
    if (document.querySelector(".config-page.dirty")) {
      const decision = await invoke("confirm_settings_changes");
      if (decision === "confirm") {
        for (const page of document.querySelectorAll(".config-page.dirty")) {
          if (!await saveConfiguration(page.querySelector("[data-save]"))) {
            status.textContent = "A section could not be saved. Review its error; your draft is preserved.";
            status.hidden = false;
            return false;
          }
        }
        if (document.querySelector(".config-page.dirty")) {
          status.textContent = "New edits are still unsaved. Review them before closing.";
          status.hidden = false;
          return false;
        }
      } else if (decision !== "discard") {
        return false;
      }
      if (pendingSaves.size) {
        status.textContent = "A save is still running. Wait for its result before closing.";
        status.hidden = false;
        return false;
      }
    }
    return await leaveSettings();
  } catch (error) {
    status.textContent = `Couldn't close settings. ${friendlyError(error)}`;
    status.hidden = false;
    return false;
  } finally {
    closeDecisionPending = false;
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
    if (action === "prepare-sandbox-image") {
      button.disabled = true;
      button.textContent = "Starting…";
      try {
        await invoke("prepare_sandbox_image", { id: nextId("sandbox-prepare") });
      } catch (error) {
        // Put the offer back. Without this the button stayed disabled reading
        // "Starting…" for a download that never started, and the only way to
        // try again was to reopen Settings.
        renderSandboxImage(store.snapshot?.sandbox_images);
        throw error;
      }
      // Not re-enabled on success: the `sandbox-images` broadcast arrives with
      // `downloading` and renders the panel, and re-enabling it would offer a
      // second download of what is already being fetched.
      renderSandboxImage({ state: "downloading", detail: "" });
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
