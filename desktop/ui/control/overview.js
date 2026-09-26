// The Overview page, and the panels Diagnostics and This computer share with it.

import {
  $,
  LOCAL_MODE,
  escapeHtml,
  invoke,
  serviceHtml,
  setDot,
  store,
  summaryHtml,
  toast,
} from "./core.js";
import { exposureCopy, modeLabel, renderSharingControls } from "./sharing.js";

export function render() {
  if (!store.snapshot) return;
  const services = store.snapshot.services || [];
  const appReady = Boolean(store.snapshot.state?.ready) && services.length > 0 && services.every((service) => service.running);
  const runtimeReady = Boolean(store.snapshot.managed_runtime);
  const sharing = store.snapshot.sharing || {};
  const sharingMode = sharing.mode || "this_computer";

  // The channel, when it is not stable. A nightly and a release both report
  // the same version with the same bundle id, so this is the only thing that
  // answers "what are you running?" in a support conversation.
  const channel = store.appUpdate && store.appUpdate.channel !== "stable" ? ` · ${store.appUpdate.channel}` : "";
  $("release").textContent = `Release ${store.snapshot.release || "development"}${channel}`;
  $("metric-app").textContent = appReady ? "Healthy" : store.state?.running ? "Starting" : "Stopped";
  $("metric-exposure").textContent = modeLabel(sharingMode);
  $("metric-exposure-detail").textContent = exposureCopy(sharingMode);

  const pill = $("state-pill");
  pill.textContent = appReady && runtimeReady ? "Healthy" : store.state?.status || "Checking";
  pill.className = `state-pill ${appReady && runtimeReady ? "ok" : store.state?.last_error ? "bad" : "warn"}`;
  setDot("overview", appReady ? "ok" : "warn");
  if (!LOCAL_MODE) {
    pill.textContent = store.snapshot.agent_host?.running ? "Coding agents running" : "Coding agents stopped";
    pill.className = `state-pill ${store.snapshot.agent_host?.running ? "ok" : "warn"}`;
  }

  const attention = [];
  if (!appReady) attention.push({ title: "Application services need attention", copy: "Reconcile or restart them below, or open Recovery.", page: "recovery" });
  if (sharing.last_error) attention.push({ title: "Sharing needs attention", copy: sharing.last_error, page: "overview" });
  const banner = $("attention-banner");
  banner.hidden = attention.length === 0;
  if (attention.length) {
    $("attention-title").textContent = attention[0].title;
    $("attention-copy").textContent = attention[0].copy;
    $("attention-action").dataset.page = attention[0].page;
  }
  $("overview-attention").innerHTML = attention.length
    ? attention.map((item) => summaryHtml(item.title, item.copy, "Review", item.page)).join("")
    : summaryHtml("Nothing urgent", "Application health checks passed. Settings for this computer are in Lemma: Settings → This Mac.", "Good", "");
  $("overview-exposure").innerHTML = summaryHtml(
    modeLabel(sharingMode),
    sharing.canonical_url || store.snapshot.state?.url || "Local address unavailable",
    sharingMode === "this_computer" ? "Private" : "Active",
    "",
  );
  renderSharingControls(sharing);

  const processHtml = services.map((service) => serviceHtml(
    service.id,
    service.pid ? `PID ${service.pid}` : service.last_exit || "Not running",
    service.running ? "running" : service.circuit_open ? "failed" : "stopped",
    service.running ? "ok" : service.circuit_open ? "bad" : "",
  )).join("");
  const embeddings = store.snapshot.capabilities?.capabilities?.embeddings;
  const capabilityHtml = embeddings
    ? serviceHtml("Semantic search", embeddings.detail || "Optional local embeddings", embeddings.status, embeddings.status === "ready" ? "ok" : embeddings.status === "degraded" ? "bad" : "")
    : "";
  $("overview-services").innerHTML = processHtml + capabilityHtml || "<p class=\"hint\">No application processes are running.</p>";

  $("diag-paths").textContent = store.snapshot.paths
    ? `Control  ${store.snapshot.paths.locald}\nLogs     ${store.snapshot.paths.logs}`
    : "Paths unavailable";
  const workspaceUrl = store.snapshot.state?.url || store.state?.url;
  const apiUrl = store.snapshot.state?.api_url || store.state?.api_url;
  if (workspaceUrl && apiUrl) {
    $("network-contract").innerHTML = `Main UI<br><code>${escapeHtml(workspaceUrl)}</code><br><br>API<br><code>${escapeHtml(apiUrl)}</code><br><br>Private services<br><code>loopback only · never proxied</code>`;
  }
  renderAgentHost(store.snapshot.agent_host || {});
}

// The anonymous install-health switch.
//
// Read once, when Local settings opens. It is not part of the operator config
// -- it is a choice about this installation, stored beside the install id --
// so it neither joins the dirty-section machinery nor waits for a save.
let telemetryLoaded = false;
export async function loadTelemetry() {
  if (telemetryLoaded) return;
  telemetryLoaded = true;
  let status;
  try {
    status = await invoke("telemetry_status");
  } catch {
    // A build that cannot answer offers nothing rather than a dead switch.
    return;
  }
  if (!status?.available) return;
  const panel = $("telemetry-panel");
  const box = $("telemetry-enabled");
  panel.hidden = false;
  box.checked = Boolean(status.enabled);
  $("telemetry-detail").textContent =
    `Sent to ${status.host}, identified only by a random id for this installation`
    + (status.install_id ? ` (${status.install_id.slice(0, 8)}…).` : ".")
    + " Turning this off is remembered, and nothing is sent again.";
  box.addEventListener("change", async () => {
    const wanted = box.checked;
    try {
      await invoke("set_telemetry_enabled", { enabled: wanted });
    } catch (error) {
      box.checked = !wanted;
      toast(String(error), "bad");
    }
  });
}

// Matches `formatUptime` in the workspace's own This computer card, which is
// the other place the same number is shown. "1434s uptime" is a reading of a
// field, not a sentence, and under a minute the number says nothing worth the
// space it takes.
function formatUptime(seconds) {
  if (!seconds || seconds < 60) return null;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `up ${hours}h ${minutes}m` : `up ${minutes}m`;
}

// The workspace's own "This Mac" card says the same states in the same words
// (`describeThisComputer` in lemma-frontend/src/desktop/this-computer.ts); this
// is the view for when the workspace will not load. It names only controls
// that exist: Restart and Open log here, and the button that opens the
// coding agents in Lemma. There is no switch to turn this on and nothing to
// connect by hand -- Lemma connects this computer itself when someone signs
// in.
export function describeAgentHost(agentHost) {
  const targets = Array.isArray(agentHost.targets) ? agentHost.targets : [];
  const connected = targets.some((target) => target.connection_state === "ONLINE");
  const activeRuns = targets.reduce((total, target) => total + (target.active_runs || 0), 0);
  const failure = targets.find((target) => target.last_error)?.last_error || agentHost.last_error || "";
  // Reachability, not liveness: an unpaired host and one that cannot reach its
  // workspace are both live processes that will never pick up a run.
  let status = "not available";
  let tone = "bad";
  let detail = "This copy of Lemma can’t run coding agents. Update Lemma to get them.";
  if (agentHost.available && !agentHost.running) {
    // The supervisor stops restarting a service that keeps crashing, and says
    // so; only Restart brings it back.
    const stopped = agentHost.restart_circuit_open || agentHost.last_error;
    status = stopped ? "not running" : "starting";
    tone = stopped ? "bad" : "";
    detail = stopped
      ? "The coding-agent service stopped and isn’t restarting on its own. Press Restart, or open the log to see why."
      : "Bringing this computer online.";
  } else if (agentHost.available && !agentHost.paired) {
    status = "not connected";
    tone = "";
    detail = "Open Lemma and sign in; this computer connects itself.";
  } else if (agentHost.available && connected) {
    status = "connected";
    tone = "ok";
    const uptime = formatUptime(agentHost.uptime_seconds);
    const running = activeRuns > 0
      ? `Running ${activeRuns} task${activeRuns === 1 ? "" : "s"}`
      : "Ready for work";
    detail = uptime ? `${running} · ${uptime}` : running;
  } else if (agentHost.available && /needs a newer Agent Host|newer Agent Host protocol|Agent Host protocol \d+ is unsupported|upgrade required/i.test(failure)) {
    status = "update needed";
    tone = "bad";
    detail = "Your workspace needs a newer Lemma app. Check for updates on the Overview page.";
  } else if (agentHost.available) {
    status = failure ? "unreachable" : "reconnecting";
    tone = "";
    detail = failure
      ? "Can’t reach your workspace right now. Lemma keeps trying; the log says why."
      : "Trying to reach your workspace.";
  }
  return { status, tone, detail };
}

export function renderAgentHost(agentHost) {
  const { status, tone, detail } = describeAgentHost(agentHost);
  $("agent-host-status").innerHTML = serviceHtml("Coding agents", detail, status, tone);
}
