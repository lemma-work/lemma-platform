// The daemon's snapshots and events, and keeping them coming.

import { $, friendlyError, invoke, nextId, store, toast } from "./core.js";
import { loadRuntimeInfo } from "./updates.js";
import { render, renderAgentHost } from "./overview.js";

/* The page that exists to explain a problem must not be the page that gives up.
 *
 * Local settings is often opened *because* the stack is broken, so a failed
 * first request must not be the last one: it retries until a snapshot arrives,
 * and says so on screen while it does. Only a live daemon sends events, so
 * waiting for one to ask again would wait forever.
 */
// Backed off rather than flat. A daemon that is coming back does so within a
// second or two; one that is not -- a stopped stack, a machine asleep with this
// window open -- should not be asked at a fixed rate for ever.
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
  "control.snapshot": ["state"],
};

function hasPath(value, path) {
  return path
    .split(".")
    .reduce((current, key) => (current == null ? undefined : current[key]), value) !== undefined;
}

// Events cross the bridge from the daemon and were read here unparsed. A
// shape this page did not expect threw partway through a branch, after some of
// that branch had already run, leaving stale state on screen with no error.
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
    render();
  }
  if (event.event === "error") {
    store.sharingBusy = false;
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
    render();
    toast(event.sharing?.mode === "this_computer" ? "Sharing stopped. Lemma is private to this computer." : "Sharing is active.");
    requestSnapshot();
  }
  if (event.event === "agent-host.status" && event.agent_host && store.snapshot) {
    store.snapshot.agent_host = event.agent_host;
    renderAgentHost(store.snapshot.agent_host);
  }
  if (["status", "state", "ready", "phase", "done", "agent-host.status"].includes(event.event)) {
    requestSnapshot();
    if (event.event === "ready") loadRuntimeInfo();
  }
}
