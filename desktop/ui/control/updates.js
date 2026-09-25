// Updating this app from Local settings, for when the workspace -- where
// This Mac → Updates lives -- cannot load, or is a hosted one.

import {
  $,
  LOCAL_MODE,
  friendlyError,
  invoke,
  store,
  toast,
} from "./core.js";

/* ------------------------------------------------------------- updates ---
 * The check runs in Rust, which is why the webview's CSP does not have to
 * learn about github.com.
 */

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return null;
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

function renderAppUpdate() {
  if (!store.appUpdate) return;
  const summary = $("app-update-summary");
  const available = $("app-update-available");
  const unsupported = $("app-update-unsupported");
  // Reset first. The branches below return early, and a warning left over from
  // a previous render is invisible only for as long as it stays nested inside
  // the hidden banner -- which is not a property worth depending on.
  $("app-update-reset-warning").hidden = true;
  const channelSuffix = store.appUpdate.channel === "stable" ? "" : ` · ${store.appUpdate.channel}`;
  const commit = store.appUpdate.buildCommit ? ` (${store.appUpdate.buildCommit.slice(0, 8)})` : "";
  summary.textContent = `Lemma ${store.appUpdate.currentVersion}${channelSuffix}${commit}`;

  if (!store.appUpdate.updatesSupported) {
    // Visible, not silently missing: a build that cannot update itself should
    // say so, or the absence reads as "there is nothing new".
    available.hidden = true;
    unsupported.hidden = false;
    unsupported.textContent =
      store.appUpdate.channel === "nightly"
        ? "Nightly builds don't update themselves. Download a newer one from the releases page when you need it."
        : "This is a development build, so it doesn't update itself.";
    return;
  }
  unsupported.hidden = true;

  if (!store.appUpdate.availableVersion) {
    available.hidden = true;
    summary.textContent += " · up to date";
    return;
  }
  available.hidden = false;
  $("app-update-headline").textContent = `Lemma ${store.appUpdate.availableVersion} is available.`;
  // Honest about what follows the restart. The app payload is small; the
  // runtime it then fetches is two orders of magnitude larger, and a user on a
  // hotspot deserves to know before committing rather than after.
  const runtime = formatBytes(store.appUpdate.runtimeDownloadBytes);
  $("app-update-cost").textContent = runtime
    ? `The update itself is small. After Lemma restarts it downloads about ${runtime} of runtime before the workspace opens.`
    : "After Lemma restarts it downloads its runtime once before the workspace opens.";
  if (!LOCAL_MODE) $("app-update-cost").textContent = "Updates the desktop app and its local agent support. Cloud mode does not download the complete Local Lemma stack.";
  // Only a new Postgres major is refused -- the one change a migration cannot
  // carry. Everything else is an ordinary update: data stays where it is and
  // migrations run on the next start.
  const blocked = store.appUpdate.dataCompatibility === "postgres-major-change";
  const warning = $("app-update-reset-warning");
  warning.hidden = !blocked;
  if (blocked) warning.textContent = postgresMajorChangeMessage(store.appUpdate);
  document.querySelector('[data-action="install-app-update"]').disabled = blocked;
}

/** What the refused update would change, in the words the shell uses too. */
export function postgresMajorChangeMessage(update) {
  const from = update?.installedPostgresMajor;
  const to = update?.candidatePostgresMajor;
  const change = from && to ? `from Postgres ${from} to Postgres ${to}` : "to a different Postgres version";
  return `This update moves Lemma's database ${change}, which Lemma can't migrate automatically yet. `
    + "Nothing was changed: your current version, pods, files and accounts are as they were.";
}

export async function loadAppUpdate() {
  try {
    store.appUpdate = await invoke("check_for_app_update");
    renderAppUpdate();
  } catch (error) {
    const summary = $("app-update-summary");
    if (summary) summary.textContent = `Couldn't check for updates. ${friendlyError(error)}`;
  }
}

// What the runtime record is read for here: whether a repair is possible at
// all. The version cards it used to fill moved to This Mac → Updates.
function renderRuntime() {
  if (!store.runtimeInfo) return;
  document.querySelectorAll('[data-action="repair-runtime"]').forEach((item) => {
    item.disabled = !LOCAL_MODE || !store.runtimeInfo.repairAvailable;
  });
}

export async function loadRuntimeInfo() {
  try {
    store.runtimeInfo = await invoke("runtime_info");
    renderRuntime();
  } catch (error) {
    toast(friendlyError(error), true);
  }
}
