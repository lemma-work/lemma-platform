// The Overview and Runtime pages, and the panels they share.

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
import { exposureCopy, modeLabel, renderSharing } from "./sharing.js";

export function render() {
  if (!store.snapshot?.operator) return;
  const readiness = store.snapshot.operator.readiness;
  const services = store.snapshot.services || [];
  const appReady = Boolean(store.snapshot.state?.ready) && services.length > 0 && services.every((service) => service.running);
  const aiReady = readiness.ai === "ready";
  const runtimeReady = Boolean(store.snapshot.managed_runtime);
  const sharing = store.snapshot.sharing || {};
  const sharingMode = sharing.mode || "this_computer";

  // The channel, when it is not stable. A nightly and a release both report
  // the same version with the same bundle id, so this is the only thing that
  // answers "what are you running?" in a support conversation.
  const channel = store.appUpdate && store.appUpdate.channel !== "stable" ? ` · ${store.appUpdate.channel}` : "";
  $("release").textContent = `Release ${store.snapshot.release || "development"}${channel}`;
  $("metric-app").textContent = appReady ? "Healthy" : store.state?.running ? "Starting" : "Stopped";
  $("metric-ai").textContent = aiReady ? "Ready" : "Not configured";
  $("metric-ai-detail").textContent = aiReady
    ? `${store.snapshot.operator.config.ai.default_model || "Provider configured"}`
    : "Use an installed coding agent, or configure an API provider or local model server.";
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
  setDot("ai", aiReady ? "ok" : "warn");
  setDot("sharing", sharing.phase === "error" ? "bad" : sharingMode === "this_computer" ? "ok" : "warn");
  setDot("integrations", readiness.integrations === "configured" ? "ok" : "");
  setDot("channels", readiness.surfaces === "configured" ? "ok" : "");
  setDot("runtime", appReady ? "ok" : "warn");

  const attention = [];
  if (!appReady) attention.push({ title: "Application services need attention", copy: "Review the runtime state and reconcile services.", page: "runtime" });
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
    : summaryHtml("Nothing urgent", "Application health checks passed. Agent setup is available in your workspace.", "Good", "");
  $("overview-exposure").innerHTML = summaryHtml(
    modeLabel(sharingMode),
    sharing.canonical_url || store.snapshot.state?.url || "Local address unavailable",
    sharingMode === "this_computer" ? "Private" : "Active",
    "sharing",
  );

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
  const allServices = processHtml + capabilityHtml || "<p class=\"hint\">No application processes are running.</p>";
  $("overview-services").innerHTML = allServices;
  $("service-list").innerHTML = allServices;

  $("diag-paths").textContent = store.snapshot.paths
    ? `Control  ${store.snapshot.paths.locald}\nLogs     ${store.snapshot.paths.logs}`
    : "Paths unavailable";
  const workspaceUrl = store.snapshot.state?.url || store.state?.url;
  const apiUrl = store.snapshot.state?.api_url || store.state?.api_url;
  if (workspaceUrl && apiUrl) {
    $("network-contract").innerHTML = `Main UI<br><code>${escapeHtml(workspaceUrl)}</code><br><br>API<br><code>${escapeHtml(apiUrl)}</code><br><br>Private services<br><code>loopback only · never proxied</code>`;
    $("connector-callback").textContent = `${apiUrl.replace(/\/$/, "")}/api/v1/connectors/oauth/callback`;
  }
  renderAgentHost(store.snapshot.agent_host || {});
  renderSharing(sharing);
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

/**
 * What the sandbox panel says, and whether the download is worth offering.
 *
 * `not-prepared` is the only state where the button does something useful:
 * `ready` has nothing left to fetch, `downloading` is already doing it, and
 * `unsupported` means there is no guest that could hold an image at all.
 */
function sandboxImageWording(imageState) {
  if (imageState === "ready") {
    return { text: "Downloaded. Pods can run code, shells and browsers on this computer.", offer: false };
  }
  if (imageState === "downloading") {
    return { text: "Downloading…", offer: false };
  }
  if (imageState === "failed") {
    return { text: "The last download did not finish. The first task in a pod will fetch it, or try again here.", offer: true };
  }
  if (imageState === "unsupported") {
    return { text: "This installation runs no private runtime, so there is no sandbox image to download.", offer: false };
  }
  if (imageState === "not-prepared") {
    return { text: "Not downloaded. Coding agents run natively and do not need it; download it to run pod code, shells and browsers here.", offer: true };
  }
  return { text: "Checking…", offer: false };
}

export function renderSandboxImage(status) {
  const label = $("sandbox-image-state");
  const detail = $("sandbox-image-detail");
  const button = document.querySelector('[data-action="prepare-sandbox-image"]');
  if (!label || !button) return;
  const imageState = status?.state || "";
  const wording = sandboxImageWording(imageState);
  label.textContent = wording.text;
  if (detail) {
    detail.textContent = status?.detail || "";
    detail.hidden = !status?.detail;
  }
  button.disabled = !wording.offer;
  button.textContent = imageState === "failed" ? "Try the download again" : "Download sandbox image";
}
