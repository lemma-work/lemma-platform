// The daemon's snapshots and events, and keeping them coming.

import {
  $,
  draftVersions,
  friendlyError,
  invoke,
  nextId,
  pendingSaves,
  sectionRevisions,
  store,
  toast,
} from "./core.js";
import { loadRuntimeInfo } from "./updates.js";
import { fillConfiguration, setSectionError } from "./config.js";
import { render, renderAgentHost, renderSandboxImage } from "./overview.js";

/* The page that exists to explain a problem must not be the page that gives up.
 *
 * This used to be called once on load and thereafter only from the daemon
 * event handler -- which needs a live daemon. So if the first call rejected,
 * nothing ever asked again: `render()` returns early with no snapshot, the
 * state pill stays "Connecting…" forever, and the only feedback is a toast that
 * clears itself after five seconds. That is the state a user reaches by opening
 * Local settings *because* the stack is broken.
 *
 * Now it retries on a heartbeat until a snapshot arrives, and says so on screen
 * while it is trying.
 */
// Backed off rather than flat. A daemon that is coming back does so within a
// second or two, and one that is not is usually not coming back at all -- a
// stopped stack, a crash loop, a machine going to sleep with this window open.
// A fixed five seconds asked that question for ever at the same rate, which is
// a wake-up every five seconds on a laptop lid nobody has opened. Starting
// sooner also makes the ordinary case feel faster than the flat interval did.
const SNAPSHOT_RETRY_FLOOR_MS = 1000;
const SNAPSHOT_RETRY_CEILING_MS = 30000;
let snapshotRetryDelay = SNAPSHOT_RETRY_FLOOR_MS;
let snapshotRetryTimer = null;

let snapshotTimer = null;

export function requestSnapshot() {
  clearTimeout(snapshotTimer);
  snapshotTimer = setTimeout(() => {
    invoke("control_snapshot", { id: nextId("snapshot") }).catch((error) => {
      showSnapshotUnavailable(String(error));
      scheduleSnapshotRetry();
    });
  }, 100);
}

export function scheduleSnapshotRetry() {
  if (snapshotRetryTimer) return;
  const delay = snapshotRetryDelay;
  snapshotRetryDelay = Math.min(snapshotRetryDelay * 2, SNAPSHOT_RETRY_CEILING_MS);
  snapshotRetryTimer = setTimeout(() => {
    snapshotRetryTimer = null;
    requestSnapshot();
  }, delay);
}

/** Ask again now, from the Retry button, instead of on the backoff. */
export function retrySnapshotNow() {
  clearTimeout(snapshotRetryTimer);
  snapshotRetryTimer = null;
  requestSnapshot();
}

// A snapshot arrived, so the next outage starts asking quickly again. Without
// this the backoff is one-way: a window left open through a restart would keep
// the half-minute interval it had reached, and the next real outage would take
// thirty seconds to notice.
export function resetSnapshotRetry() {
  snapshotRetryDelay = SNAPSHOT_RETRY_FLOOR_MS;
  clearTimeout(snapshotRetryTimer);
  snapshotRetryTimer = null;
}

export function showSnapshotUnavailable(reason) {
  const banner = $("snapshot-unavailable");
  if (!banner) return;
  banner.hidden = false;
  const detail = $("snapshot-unavailable-detail");
  // textContent: `reason` is a daemon error string, not something to parse.
  if (detail) detail.textContent = friendlyError(reason);
  $("state-pill").textContent = "Disconnected";
  $("state-pill").className = "state-pill bad";
  $("metric-app").textContent = "Unavailable";
}

function clearSnapshotUnavailable() {
  clearTimeout(snapshotRetryTimer);
  snapshotRetryTimer = null;
  const banner = $("snapshot-unavailable");
  if (banner) banner.hidden = true;
}

// What each event has to carry before this page will act on it.
//
// Only the fields its branch dereferences without guarding, which is where a
// missing one throws. Everything else is already read with `?.` or `||`.
const REQUIRED_EVENT_FIELDS = {
  "control.snapshot": ["state", "operator.config.revision"],
  "config.applied": ["operator.config.revision"],
};

function hasPath(value, path) {
  return path
    .split(".")
    .reduce((current, key) => (current == null ? undefined : current[key]), value) !== undefined;
}

// Events cross the bridge from the daemon and were read here unparsed.
///
/// A shape this page did not expect threw partway through a branch, after
/// some of that branch had already run: a `config.applied` without an
/// `operator` released the save button and dropped the pending save, then
/// threw before `fillConfiguration`, leaving stale settings on screen with no
/// error, no toast, and a save the page believed had succeeded.
function unusableEventReason(event) {
  if (!event || typeof event !== "object" || typeof event.event !== "string") {
    return "the daemon sent something this page cannot read";
  }
  const missing = (REQUIRED_EVENT_FIELDS[event.event] || []).filter(
    (path) => !hasPath(event, path),
  );
  return missing.length ? `${event.event} arrived without ${missing.join(", ")}` : null;
}

export function handleLocaldEvent(event) {
  const unusable = unusableEventReason(event);
  if (unusable) {
    // Said, not swallowed. Showing the previous snapshot as though it were
    // current is the one outcome worse than showing nothing.
    if (event?.event === "control.snapshot" || !event?.event) {
      showSnapshotUnavailable(unusable);
    } else {
      toast(unusable, true);
      requestSnapshot();
    }
    return;
  }
  if (event.event === "control.snapshot") {
    store.snapshot = event;
    store.state = event.state;
    clearSnapshotUnavailable();
    resetSnapshotRetry();
    if (!store.sharingChoice) store.sharingChoice = store.snapshot.sharing?.mode || "this_computer";
    fillConfiguration();
    render();
    renderSandboxImage(event.sandbox_images);
    for (const [id, pending] of pendingSaves) {
      const operation = event.config_operations?.[id];
      if (operation?.status === "succeeded") {
        handleLocaldEvent({ event: "config.applied", id, operator: operation.operator });
      } else if (operation?.status === "failed" || operation?.status === "interrupted") {
        handleLocaldEvent({ event: "error", id, message: operation.message || "The daemon restarted during this save. Review the current settings before retrying; your draft is preserved." });
      } else if (!operation && Date.now() - pending.started > 10000) {
        handleLocaldEvent({ event: "error", id, message: "The save could not be confirmed. Review the current settings before retrying." });
      }
    }
    if (pendingSaves.size) scheduleSnapshotRetry();
  }
  if (event.event === "sandbox-images") {
    if (store.snapshot) store.snapshot.sandbox_images = { state: event.state, detail: event.detail };
    renderSandboxImage({ state: event.state, detail: event.detail });
  }
  if (event.event === "config.applied") {
    const pending = pendingSaves.get(event.id);
    if (pending) {
      if ((draftVersions.get(pending.page.dataset.page) || 0) === pending.version) pending.page.classList.remove("dirty");
      pending.button.disabled = false;
      pending.button.textContent = pending.original;
      pendingSaves.delete(event.id);
      pending.complete(true);
      // This acknowledged section write was conditional on our saved revision.
      // Other drafts can advance past our own change without losing their edits.
      for (const [name, revision] of sectionRevisions) {
        if (revision === pending.expectedRevision) sectionRevisions.set(name, event.operator.config.revision);
      }
    }
    if (store.snapshot && store.snapshot.operator.config.revision <= event.operator.config.revision) store.snapshot.operator = event.operator;
    fillConfiguration();
    render();
    if (pending) toast("Configuration saved and backend health checks passed.");
    requestSnapshot();
  }
  if (event.event === "error") {
    store.sharingBusy = false;
    const pending = pendingSaves.get(event.id);
    if (pending) {
      pending.button.disabled = false;
      pending.button.textContent = pending.original;
      pendingSaves.delete(event.id);
      setSectionError(pending.page, event.message || "Local operation failed");
      pending.complete(false);
    }
    toast(event.message || "Local operation failed", true);
    requestSnapshot();
  }
  if (event.event === "sharing.progress") {
    if (event.sharing && store.snapshot) store.snapshot.sharing = event.sharing;
    render();
  }
  if (event.event === "sharing.changed") {
    store.sharingBusy = false;
    if (event.sharing && store.snapshot) store.snapshot.sharing = event.sharing;
    store.sharingChoice = event.sharing?.mode || "this_computer";
    render();
    toast(event.sharing?.mode === "this_computer" ? "Sharing stopped. Lemma is private to this computer." : "Sharing is active.");
    requestSnapshot();
  }
  if (event.event === "sharing.preflight") requestSnapshot();
  if (event.event === "agent-host.status" && event.agent_host && store.snapshot) {
    store.snapshot.agent_host = event.agent_host;
    renderAgentHost(store.snapshot.agent_host);
  }
  if (["status", "state", "ready", "phase", "done", "agent-host.status"].includes(event.event)) {
    requestSnapshot();
    if (event.event === "ready") loadRuntimeInfo();
  }
}
