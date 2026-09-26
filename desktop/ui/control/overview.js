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

/* What each startup warning is called, and where its next step is.
 *
 * The daemon writes the sentence (it knows the versions); this page adds a
 * title and a button, and says it above every page -- a cloud user has no
 * Overview, and an update that stopped mid-migration matters more than
 * whichever page they opened.
 */
const STARTUP_WARNINGS = {
  "update-interrupted": { title: "Your last update didn't finish", action: "Check for updates", target: "updates" },
  "update-record-unreadable": { title: "Lemma couldn't read an update in progress", action: "Check for updates", target: "updates" },
  "settings-writes-disabled": { title: "Settings changes are turned off", action: "Open Diagnostics", target: "diagnostics" },
};
const REPAIRED = { title: "Lemma repaired something while starting", action: "Open Diagnostics", target: "diagnostics" };

export function startupWarningCopy(warning) {
  return STARTUP_WARNINGS[warning?.code] || REPAIRED;
}

export function readStartupWarnings(snapshot) {
  const warnings = Array.isArray(snapshot?.warnings) ? snapshot.warnings : [];
  return warnings.filter((warning) => warning && typeof warning.message === "string" && warning.message.trim());
}

export function renderStartupWarnings(warnings) {
  const holder = $("startup-warnings");
  if (!holder) return;
  holder.hidden = warnings.length === 0;
  holder.innerHTML = warnings.map((warning) => {
    const copy = startupWarningCopy(warning);
    return `<div class="warning-box" role="alert" data-warning-code="${escapeHtml(warning.code)}">`
      + `<strong>${escapeHtml(copy.title)}</strong><p>${escapeHtml(warning.message)}</p>`
      + `<div class="button-row"><button class="btn compact" type="button" data-goto="${escapeHtml(copy.target)}">${escapeHtml(copy.action)}</button></div>`
      + "</div>";
  }).join("");
}

/* The background service stopped answering.
 *
 * The last snapshot is still in memory, and drawing it -- "Healthy", every
 * service "running" -- while nothing is answering told someone who opened this
 * page because Lemma was broken that it was fine. Said instead, and replaced
 * by the next snapshot that arrives.
 */
const NOT_ANSWERING = "Lemma's background service isn't answering";
export function renderDisconnected() {
  $("metric-app").textContent = "Not answering";
  $("metric-app-detail").textContent = "Health below is unknown until it answers again.";
  setDot("overview", "bad");
  $("attention-banner").hidden = true;
  $("overview-attention").innerHTML = summaryHtml(
    NOT_ANSWERING,
    "Nothing on this page is current. Try again, or restart Lemma from Recovery.",
    "Review",
    "recovery",
  );
  $("overview-services").innerHTML = `<p class="hint">${escapeHtml(NOT_ANSWERING)}, so the state of Lemma's services is unknown.</p>`;
  $("agent-host-status").innerHTML = serviceHtml("Lemma Agent Host", `${NOT_ANSWERING}, so this is unknown.`, "unknown", "");
}

export function render() {
  if (!store.snapshot) return;
  const warnings = readStartupWarnings(store.snapshot);
  renderStartupWarnings(warnings);
  $("metric-app-detail").textContent = "Backend, frontend, and private dependencies.";
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
    pill.textContent = store.snapshot.agent_host?.running ? "Agent Host running" : "Agent Host stopped";
    pill.className = `state-pill ${store.snapshot.agent_host?.running ? "ok" : "warn"}`;
  }

  const attention = [];
  for (const warning of warnings) {
    const copy = startupWarningCopy(warning);
    attention.push({ title: copy.title, copy: warning.message, page: copy.target });
  }
  if (!appReady) attention.push({ title: "Application services need attention", copy: "Use Start missing services below, restart the application, or open Recovery.", page: "services" });
  // "sharing" is not a page: it goes to the Return to This computer button,
  // which is the one sharing action this window has.
  if (sharing.last_error) attention.push({ title: "Sharing needs attention", copy: sharing.last_error, page: "sharing" });
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

export function renderAgentHost(agentHost) {
  const targets = Array.isArray(agentHost.targets) ? agentHost.targets : [];
  const connected = targets.some((target) => target.connection_state === "ONLINE");
  const activeRuns = targets.reduce((total, target) => total + (target.active_runs || 0), 0);
  // Reachability, not liveness: an unpaired host and one that cannot reach its
  // workspace are both live processes that will never pick up a run.
  let status = "unavailable";
  let tone = "bad";
  let detail = "This build of Lemma does not include the Agent Host.";
  if (agentHost.available && !agentHost.running) {
    status = "off";
    tone = "";
    detail = agentHost.last_error || "Turn it on from Lemma to run coding agents here.";
  } else if (agentHost.available && !agentHost.paired) {
    status = "not connected";
    tone = "";
    detail = "Connect this computer from Lemma to start running agents on it.";
  } else if (agentHost.available && connected) {
    status = "connected";
    tone = "ok";
    const uptime = formatUptime(agentHost.uptime_seconds);
    const running = activeRuns > 0
      ? `Running ${activeRuns} task${activeRuns === 1 ? "" : "s"}`
      : "Ready for work";
    detail = uptime ? `${running} · ${uptime}` : running;
  } else if (agentHost.available) {
    status = "reconnecting";
    tone = "";
    detail = targets[0]?.last_error || agentHost.last_error || "Trying to reach the workspace.";
  }
  $("agent-host-status").innerHTML = serviceHtml("Lemma Agent Host", detail, status, tone);
}
