const tauri = window.__TAURI__;
const invoke = (command, args = {}) => tauri.core.invoke(command, args);
const listen = (event, handler) => tauri.event.listen(event, ({ payload }) => handler(payload));
const $ = (id) => document.getElementById(id);
const csv = (value) => value.split(",").map((item) => item.trim()).filter(Boolean);

const titles = {
  overview: ["Overview", "Health, attention, and exposure at a glance."],
  ai: ["AI provider", "Choose and validate the system model profile used by local agents."],
  sharing: ["Sharing", "Keep Lemma private, use it on trusted Wi-Fi, or create an intentional public link."],
  integrations: ["Integrations", "Configure service connections without mixing them with login or channel credentials."],
  channels: ["Channels", "Make agents reachable through only the receivers you explicitly enable."],
  runtime: ["Runtime", "Application health, lifecycle controls, and private dependency status."],
  updates: ["Updates", "Exact release matching, verified packs, and safe repair boundaries."],
  diagnostics: ["Diagnostics", "Local paths, canonical origins, logs, and non-destructive repair."],
};

let snapshot = null;
let state = null;
let runtimeInfo = null;
let filling = false;
let requestCounter = 0;
let sharingChoice = null;
let sharingProvider = "ngrok";
let cloudflareSetupChoice = null;
let sharingBusy = false;
let snapshotTimer = null;

const nextId = (prefix) => `control-${prefix}-${Date.now()}-${++requestCounter}`;

function toast(message, error = false) {
  const element = $("toast");
  element.textContent = message;
  element.className = `toast${error ? " error" : ""}`;
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.hidden = true; }, 5500);
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function setPage(page) {
  if (!titles[page]) return;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.dataset.page === page);
  });
  $("page-title").textContent = titles[page][0];
  $("page-subtitle").textContent = titles[page][1];
  document.querySelector(".content").scrollTo({ top: 0, behavior: "instant" });
}

async function closeLocalSettings() {
  try {
    await invoke("close_local_settings");
  } catch (error) {
    toast(String(error), true);
  }
}

function markDirty(target) {
  if (filling) return;
  const page = target.closest(".config-page");
  if (page) page.classList.add("dirty");
}

function secretInputs() {
  return [...document.querySelectorAll("input[data-secret]")];
}

function labelSecretButton(button, input) {
  if (!button || !input) return;
  const label = input.labels?.[0]?.textContent?.trim() || "credential";
  button.setAttribute("aria-label", `${button.textContent} ${label}`);
}

function configureInteractionHandlers() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setPage(button.dataset.page));
  });
  $("back-to-lemma").addEventListener("click", closeLocalSettings);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.defaultPrevented) closeLocalSettings();
  });
  document.querySelectorAll(".config-page input, .config-page select").forEach((input) => {
    input.addEventListener("input", () => markDirty(input));
    input.addEventListener("change", () => markDirty(input));
  });
  document.querySelectorAll(".secret-clear").forEach((button) => {
    const input = button.parentElement.querySelector("input[data-secret]");
    labelSecretButton(button, input);
    button.addEventListener("click", (event) => {
      event.preventDefault();
      input.value = "";
      input.dataset.clear = input.dataset.clear === "true" ? "false" : "true";
      button.classList.toggle("armed", input.dataset.clear === "true");
      button.textContent = input.dataset.clear === "true" ? "Keep" : "Remove";
      labelSecretButton(button, input);
      markDirty(button);
    });
  });
  document.querySelectorAll("[data-save]").forEach((button) => {
    button.addEventListener("click", () => saveConfiguration(button));
  });
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => runDesktopAction(button));
  });
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", () => copyText($(button.dataset.copyTarget).textContent));
  });
  $("attention-action").addEventListener("click", () => {
    const page = $("attention-action").dataset.page || "ai";
    setPage(page);
  });
  $("detect-ollama").addEventListener("click", () => {
    $("ai-protocol").value = "openai_compat";
    $("ai-base").value = "http://127.0.0.1:11434/v1";
    $("ai-key").value = "";
    $("ai-private-network").checked = false;
    markDirty($("ai-base"));
    toast("Ollama selected. Validate & apply will discover its models.");
  });
  $("detect-lmstudio").addEventListener("click", () => {
    $("ai-protocol").value = "openai_compat";
    $("ai-base").value = "http://127.0.0.1:1234/v1";
    $("ai-key").value = "";
    $("ai-private-network").checked = false;
    markDirty($("ai-base"));
    toast("LM Studio selected. Start its server, then validate.");
  });
  document.querySelectorAll("[data-sharing-mode]").forEach((button) => {
    button.addEventListener("click", () => selectSharingChoice(button.dataset.sharingMode));
  });
  document.querySelectorAll("[data-provider]").forEach((button) => {
    button.addEventListener("click", () => {
      sharingProvider = button.dataset.provider;
      document.querySelectorAll("[data-provider]").forEach((candidate) => {
        candidate.classList.toggle("active", candidate.dataset.provider === sharingProvider);
      });
      renderSharing(snapshot?.sharing);
      invoke("sharing_action", {
        action: "preflight",
        id: nextId("sharing-preflight"),
        payload: { provider: sharingProvider },
      }).catch((error) => toast(String(error), true));
    });
  });
  $("cloudflare-setup").addEventListener("change", () => {
    cloudflareSetupChoice = $("cloudflare-setup").value;
    renderSharing(snapshot?.sharing);
  });
  $("sharing-enable-lan").addEventListener("click", enableLanSharing);
  $("sharing-enable-public").addEventListener("click", enablePublicSharing);
  $("sharing-disable").addEventListener("click", disableSharing);
  $("public-confirm-cancel").addEventListener("click", () => $("public-confirm-dialog").close());
  $("public-confirm-activate").addEventListener("click", activatePublicSharing);
}

function fillConfiguration() {
  if (!snapshot?.operator) return;
  filling = true;
  const config = snapshot.operator.config;
  const presence = snapshot.operator.secrets || {};
  $("ai-protocol").value = config.ai.protocol;
  $("ai-base").value = config.ai.base_url;
  $("ai-model").value = config.ai.default_model;
  $("ai-models").value = config.ai.models.join(", ");
  $("ai-vision").value = config.ai.vision_models.join(", ");
  $("ai-private-network").checked = Boolean(config.ai.allow_private_network);
  $("ai-validation").textContent = config.ai.last_validated_at_unix_ms
    ? `Validated ${new Date(config.ai.last_validated_at_unix_ms).toLocaleString()}`
    : "Not validated";
  $("composio-enabled").checked = config.integrations.composio_enabled;
  $("google-id").value = config.integrations.google_client_id;
  $("microsoft-id").value = config.integrations.microsoft_client_id;
  $("slack-enabled").checked = config.surfaces.slack_socket_mode;
  $("telegram-enabled").checked = config.surfaces.telegram_polling;
  $("teams-id").value = config.surfaces.teams_app_id;
  $("teams-tenant").value = config.surfaces.teams_tenant_id;
  $("wa-phone").value = config.surfaces.whatsapp_phone_number_id;
  $("wa-waba").value = config.surfaces.whatsapp_waba_id;
  $("resend-domain").value = config.surfaces.resend_inbound_domain;
  secretInputs().forEach((input) => {
    input.value = "";
    input.dataset.clear = "false";
    input.placeholder = presence[input.dataset.secret] ? "Configured — enter to replace" : "Not configured";
    const button = input.parentElement.querySelector(".secret-clear");
    if (button) {
      button.classList.remove("armed");
      button.textContent = "Remove";
      button.disabled = !presence[input.dataset.secret];
      labelSecretButton(button, input);
    }
  });
  document.querySelectorAll(".config-page").forEach((page) => page.classList.remove("dirty"));
  filling = false;
}

function collectConfiguration() {
  const config = structuredClone(snapshot.operator.config);
  config.ai = {
    ...config.ai,
    protocol: $("ai-protocol").value,
    base_url: $("ai-base").value.trim(),
    default_model: $("ai-model").value.trim(),
    models: csv($("ai-models").value),
    vision_models: csv($("ai-vision").value),
    allow_private_network: $("ai-private-network").checked,
  };
  config.integrations = {
    ...config.integrations,
    composio_enabled: $("composio-enabled").checked,
    google_client_id: $("google-id").value.trim(),
    microsoft_client_id: $("microsoft-id").value.trim(),
  };
  config.surfaces = {
    ...config.surfaces,
    slack_socket_mode: $("slack-enabled").checked,
    telegram_polling: $("telegram-enabled").checked,
    teams_app_id: $("teams-id").value.trim(),
    teams_tenant_id: $("teams-tenant").value.trim(),
    whatsapp_phone_number_id: $("wa-phone").value.trim(),
    whatsapp_waba_id: $("wa-waba").value.trim(),
    resend_inbound_domain: $("resend-domain").value.trim(),
  };
  const secrets = {};
  secretInputs().forEach((input) => {
    if (input.dataset.clear === "true") secrets[input.dataset.secret] = null;
    else if (input.value) secrets[input.dataset.secret] = input.value;
  });
  return { config, secrets };
}

async function saveConfiguration(button) {
  if (!snapshot || button.disabled) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await invoke("apply_operator_config", {
      id: nextId("apply"),
      payload: collectConfiguration(),
    });
  } catch (error) {
    button.disabled = false;
    button.textContent = original;
    toast(String(error), true);
  }
}

async function runDesktopAction(button) {
  try {
    const action = button.dataset.action;
    if (action === "start") await invoke("start");
    if (action === "restart") await invoke("restart");
    if (action === "stop") await invoke("stop", { includeInfra: false });
    if (action === "stop-all") {
      if (!window.confirm("Stop the Lemma application and its private runtime? Workspace data is preserved.")) return;
      await invoke("stop", { includeInfra: true });
    }
    if (action === "logs") await invoke("open_logs");
    if (action === "devtools") await invoke("open_developer_tools");
    // Connecting, choosing agents and turning it off live in the workspace, so
    // a cloud user reaches the same controls. This page keeps only what is
    // useful when the workspace itself will not load.
    if (action === "agent-host-open") {
      await invoke("close_local_settings");
      await invoke("open_app");
    }
    if (action === "agent-host-restart") await invoke("agent_host_action", { action: "restart" });
    if (action === "agent-host-log") await invoke("open_logs");
    if (action === "repair-runtime") {
      if (!window.confirm("Stop Lemma briefly and verify or replace only signed runtime files?")) return;
      document.querySelectorAll('[data-action="repair-runtime"]').forEach((item) => {
        item.disabled = true;
        item.textContent = "Repairing…";
      });
      await invoke("repair_runtime");
      await loadRuntimeInfo();
      toast("Runtime verification finished. Lemma is starting.");
    }
  } catch (error) {
    toast(String(error), true);
  } finally {
    document.querySelectorAll('[data-action="repair-runtime"]').forEach((item) => {
      item.disabled = !runtimeInfo?.repairAvailable;
      item.textContent = "Verify & repair runtime";
    });
  }
}

function render() {
  if (!snapshot?.operator) return;
  const readiness = snapshot.operator.readiness;
  const services = snapshot.services || [];
  const appReady = services.length > 0 && services.every((service) => service.running);
  const aiReady = readiness.ai === "ready";
  const runtimeReady = Boolean(snapshot.managed_runtime);
  const sharing = snapshot.sharing || {};
  const sharingMode = sharing.mode || "this_computer";

  $("release").textContent = `Release ${snapshot.release || "development"}`;
  $("metric-app").textContent = appReady ? "Healthy" : state?.running ? "Starting" : "Stopped";
  $("metric-ai").textContent = aiReady ? "Ready" : "Needs setup";
  $("metric-ai-detail").textContent = aiReady
    ? `${snapshot.operator.config.ai.default_model || "Provider configured"}`
    : "Choose a provider, or point Lemma at Ollama or LM Studio.";
  $("metric-exposure").textContent = modeLabel(sharingMode);
  $("metric-exposure-detail").textContent = exposureCopy(sharingMode);

  const pill = $("state-pill");
  pill.textContent = appReady && runtimeReady ? "Healthy" : state?.status || "Checking";
  pill.className = `state-pill ${appReady && runtimeReady ? "ok" : state?.last_error ? "bad" : "warn"}`;
  setDot("overview", appReady ? "ok" : "warn");
  setDot("ai", aiReady ? "ok" : "warn");
  setDot("sharing", sharing.phase === "error" ? "bad" : sharingMode === "this_computer" ? "ok" : "warn");
  setDot("integrations", readiness.integrations === "configured" ? "ok" : "");
  setDot("channels", readiness.surfaces === "configured" ? "ok" : "");
  setDot("runtime", appReady ? "ok" : "warn");

  const attention = [];
  if (!aiReady) attention.push({ title: "Choose an AI provider", copy: "Agents cannot answer until a provider is configured.", page: "ai" });
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
    : summaryHtml("Nothing urgent", "Lemma is healthy and ready for local work.", "Good", "");
  $("overview-exposure").innerHTML = summaryHtml(
    modeLabel(sharingMode),
    sharing.canonical_url || snapshot.state?.url || "Local address unavailable",
    sharingMode === "this_computer" ? "Private" : "Active",
    "sharing",
  );

  const processHtml = services.map((service) => serviceHtml(
    service.id,
    service.pid ? `PID ${service.pid}` : service.last_exit || "Not running",
    service.running ? "healthy" : service.circuit_open ? "failed" : "stopped",
    service.running ? "ok" : service.circuit_open ? "bad" : "",
  )).join("");
  const embeddings = snapshot.capabilities?.capabilities?.embeddings;
  const capabilityHtml = embeddings
    ? serviceHtml("Semantic search", embeddings.detail || "Optional local embeddings", embeddings.status, embeddings.status === "ready" ? "ok" : embeddings.status === "degraded" ? "bad" : "")
    : "";
  const allServices = processHtml + capabilityHtml || "<p class=\"hint\">No application processes are running.</p>";
  $("overview-services").innerHTML = allServices;
  $("service-list").innerHTML = allServices;

  $("diag-paths").textContent = snapshot.paths
    ? `Control  ${snapshot.paths.locald}\nLogs     ${snapshot.paths.logs}`
    : "Paths unavailable";
  const workspaceUrl = snapshot.state?.url || state?.url;
  const apiUrl = snapshot.state?.api_url || state?.api_url;
  if (workspaceUrl && apiUrl) {
    $("network-contract").innerHTML = `Main UI<br><code>${escapeHtml(workspaceUrl)}</code><br><br>API<br><code>${escapeHtml(apiUrl)}</code><br><br>Private services<br><code>loopback only · never proxied</code>`;
    $("connector-callback").textContent = `${apiUrl.replace(/\/$/, "")}/api/v1/connectors/oauth/callback`;
  }
  renderAgentHost(snapshot.agent_host || {});
  renderSharing(sharing);
}

function renderAgentHost(agentHost) {
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
    detail = activeRuns > 0
      ? `Running ${activeRuns} task${activeRuns === 1 ? "" : "s"} · ${agentHost.uptime_seconds || 0}s uptime`
      : `Ready for work · ${agentHost.uptime_seconds || 0}s uptime`;
  } else if (agentHost.available) {
    status = "reconnecting";
    tone = "";
    detail = targets[0]?.last_error || agentHost.last_error || "Trying to reach the workspace.";
  }
  $("agent-host-status").innerHTML = serviceHtml("Lemma Agent Host", detail, status, tone);
}

function summaryHtml(title, copy, status, page) {
  return `<div class="summary-row"${page ? ` data-summary-page="${escapeHtml(page)}"` : ""}><span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(copy)}</small></span><span class="status">${escapeHtml(status)}</span></div>`;
}

function serviceHtml(title, copy, status, tone) {
  return `<div class="service-row"><span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(copy)}</small></span><span class="status ${tone}">${escapeHtml(status)}</span></div>`;
}

function setDot(id, tone) {
  const dot = $(`dot-${id}`);
  if (dot) dot.className = `health-dot ${tone}`;
}

function modeLabel(mode) {
  if (mode === "local_network") return "Local network";
  if (mode === "public") return "Public link";
  return "This computer";
}

function exposureCopy(mode) {
  if (mode === "local_network") return "Reachable on the selected trusted Wi-Fi interface.";
  if (mode === "public") return "Reachable from the internet through your tunnel account.";
  return "Not reachable from another device.";
}

function selectSharingChoice(mode) {
  sharingChoice = mode;
  document.querySelectorAll("[data-sharing-mode]").forEach((button) => {
    const active = button.dataset.sharingMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", String(active));
  });
  $("lan-config").hidden = mode !== "local_network";
  $("public-config").hidden = mode !== "public";
}

function renderSharing(sharing = {}) {
  if (!sharingChoice) sharingChoice = sharing.mode || "this_computer";
  selectSharingChoice(sharingChoice);
  const actualMode = sharing.mode || "this_computer";
  const stackReady = Boolean(snapshot?.state?.ready && snapshot?.state?.running);
  const transition = sharingBusy
    || Boolean(sharing.transition_running)
    || !["ready", "error"].includes(sharing.phase || "ready");
  sharingBusy = transition;
  $("sharing-phase").textContent = transition ? String(sharing.phase || "working").replaceAll("_", " ") : sharing.phase === "error" ? "attention" : "ready";
  $("sharing-phase").className = `status ${sharing.phase === "error" ? "bad" : actualMode === "this_computer" ? "ok" : "warn"}`;
  $("sharing-current-mode").textContent = actualMode === "this_computer" ? "Private" : actualMode === "public" ? "Internet" : "Wi-Fi";
  $("sharing-current-copy").textContent = exposureCopy(actualMode);
  $("sharing-url").textContent = sharing.canonical_url || snapshot?.state?.url || "—";
  $("sharing-disable").hidden = actualMode === "this_computer";
  $("sharing-disable").disabled = transition;
  $("sharing-enable-lan").disabled = transition || !stackReady;
  $("sharing-enable-public").disabled = transition || !stackReady;
  const warnings = stackReady
    ? (sharing.warnings || [])
    : ["Start Lemma and wait until the local stack is healthy before enabling sharing.", ...(sharing.warnings || [])];
  $("sharing-warnings").innerHTML = warnings.map((warning) => `<div class="warning-box">${escapeHtml(warning)}</div>`).join("");

  const interfaces = sharing.interfaces || [];
  const interfaceSelect = $("sharing-interface");
  const selectedInterface = interfaceSelect.value
    || sharing.preferences?.selected_interface
    || sharing.selected_interface
    || "";
  interfaceSelect.innerHTML = `<option value="">Choose an interface</option>${interfaces.map((item) => `<option value="${escapeHtml(item.address)}">${escapeHtml(item.label)}</option>`).join("")}`;
  if (interfaces.some((item) => item.address === selectedInterface || item.name === selectedInterface)) {
    const item = interfaces.find((candidate) => candidate.address === selectedInterface || candidate.name === selectedInterface);
    interfaceSelect.value = item.address;
  }
  const qrVisible = actualMode === "local_network" && sharing.phase === "ready" && Boolean(sharing.qr_svg);
  $("sharing-qr").hidden = !qrVisible;
  $("sharing-qr-image").innerHTML = qrVisible ? sharing.qr_svg : "";

  $("cloudflare-fields").hidden = sharingProvider !== "cloudflare";
  const readiness = sharing.provider_readiness?.[sharingProvider] || {};
  renderProviderReadiness(readiness);
  if (sharingProvider === "cloudflare") {
    const setup = cloudflareSetupChoice
      || sharing.preferences?.cloudflare_setup
      || "automatic";
    $("cloudflare-setup").value = setup;
    $("cloudflare-existing-field").hidden = setup !== "existing";
    const tunnels = readiness.tunnels || [];
    const select = $("cloudflare-tunnel");
    const selected = select.value || sharing.preferences?.cloudflare_tunnel_id || "";
    select.innerHTML = `<option value="">Choose a tunnel</option>${tunnels.map((tunnel) => `<option value="${escapeHtml(tunnel.id)}" data-name="${escapeHtml(tunnel.name)}">${escapeHtml(tunnel.name)} · ${escapeHtml(tunnel.id.slice(0, 8))}</option>`).join("")}`;
    if (tunnels.some((tunnel) => tunnel.id === selected)) select.value = selected;
    if (!$("cloudflare-hostname").value) {
      $("cloudflare-hostname").value = sharing.preferences?.cloudflare_hostname || "";
    }
    const managed = Boolean(sharing.preferences?.cloudflare_tunnel_owned);
    const managedName = sharing.preferences?.cloudflare_tunnel_name || "this installation";
    const managedHostname = sharing.preferences?.cloudflare_hostname || "";
    $("cloudflare-managed-summary").hidden = setup !== "automatic";
    $("cloudflare-managed-summary").innerHTML = managed
      ? `<strong>Managed by Lemma:</strong>&nbsp;${escapeHtml(managedName)}${managedHostname ? ` · ${escapeHtml(managedHostname)}` : ""}. Disabling stops the connector but keeps this setup for reuse.`
      : "After one-time login, Lemma will create a dedicated named tunnel and DNS route, then reuse them without auto-starting.";
  }
}

function renderProviderReadiness(readiness) {
  const providerName = sharingProvider === "cloudflare" ? "cloudflared" : "ngrok";
  const ready = readiness.installed && readiness.authenticated;
  const stackReady = Boolean(snapshot?.state?.ready && snapshot?.state?.running);
  const heading = !readiness.installed
    ? `${providerName} is not installed`
    : !readiness.authenticated ? `${providerName} needs authentication`
      : `${providerName} is ready`;
  const detail = readiness.message
    || readiness.version
    || (ready ? "Lemma will use your existing local CLI configuration." : "Complete setup in Terminal, then return here.");
  const commands = (readiness.instructions || []).map((instruction) => {
    const match = instruction.match(/`([^`]+)`/);
    if (!match) return `<p>${escapeHtml(instruction)}</p>`;
    return `<div class="command-row"><code>${escapeHtml(match[1])}</code><button class="btn compact" data-copy-command="${escapeHtml(match[1])}">Copy</button></div>`;
  }).join("");
  $("provider-readiness").innerHTML = `<div class="readiness-card"><div><strong>${escapeHtml(heading)}</strong><p>${escapeHtml(detail)}</p></div><span class="status ${ready ? "ok" : "warn"}">${ready ? "ready" : "setup"}</span></div>${commands ? `<div class="command-list">${commands}</div>` : ""}`;
  document.querySelectorAll("[data-copy-command]").forEach((button) => {
    button.addEventListener("click", () => copyText(button.dataset.copyCommand));
  });
  $("sharing-enable-public").disabled = sharingBusy || !ready || !stackReady;
}

async function enableLanSharing() {
  const selectedInterface = $("sharing-interface").value;
  if (!selectedInterface) {
    toast("Choose a private IPv4 network interface.", true);
    return;
  }
  sharingBusy = true;
  renderSharing(snapshot?.sharing);
  try {
    await invoke("sharing_action", {
      action: "enable",
      id: nextId("sharing-enable-lan"),
      payload: {
        mode: "local_network",
        interface: selectedInterface,
        public_warning_confirmed: false,
      },
    });
    toast("Preparing the local-network gateway…");
  } catch (error) {
    sharingBusy = false;
    toast(String(error), true);
  }
}

function enablePublicSharing() {
  const dialog = $("public-confirm-dialog");
  if (!dialog.open) dialog.showModal();
}

async function activatePublicSharing() {
  $("public-confirm-dialog").close();
  const payload = {
    mode: "public",
    provider: sharingProvider,
    public_warning_confirmed: true,
  };
  if (sharingProvider === "cloudflare") {
    payload.cloudflare_setup = $("cloudflare-setup").value;
    payload.hostname = $("cloudflare-hostname").value.trim();
    if (!payload.hostname) {
      toast("Enter the public hostname to create in your Cloudflare zone.", true);
      return;
    }
    if (payload.cloudflare_setup === "existing") {
      const tunnel = $("cloudflare-tunnel");
      const selected = tunnel.selectedOptions[0];
      payload.cloudflare_tunnel_id = tunnel.value;
      payload.cloudflare_tunnel_name = selected?.dataset.name || selected?.textContent || "";
      if (!payload.cloudflare_tunnel_id) {
        toast("Choose an existing named tunnel.", true);
        return;
      }
    }
  }
  sharingBusy = true;
  renderSharing(snapshot?.sharing);
  try {
    await invoke("sharing_action", {
      action: "enable",
      id: nextId("sharing-enable-public"),
      payload,
    });
    toast(`Starting ${sharingProvider === "cloudflare" ? "Cloudflare" : "ngrok"} and validating the public origin…`);
  } catch (error) {
    sharingBusy = false;
    toast(String(error), true);
  }
}

async function disableSharing() {
  sharingBusy = true;
  renderSharing(snapshot?.sharing);
  try {
    await invoke("sharing_action", {
      action: "disable",
      id: nextId("sharing-disable"),
    });
    toast("Restoring This computer mode…");
  } catch (error) {
    sharingBusy = false;
    toast(String(error), true);
  }
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(String(value || ""));
  } catch (_) {
    const input = document.createElement("textarea");
    input.value = String(value || "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }
  toast("Copied.");
}

function renderRuntime() {
  if (!runtimeInfo) return;
  $("runtime-desktop-release").textContent = runtimeInfo.desktopRelease;
  $("runtime-active-release").textContent = runtimeInfo.activeRelease || "Not installed";
  $("runtime-previous-release").textContent = runtimeInfo.previousRelease || "None";
  $("runtime-source").textContent = runtimeInfo.source === "bundled"
    ? "Verified inside this signed app."
    : "Verified immutable download.";
  document.querySelectorAll('[data-action="repair-runtime"]').forEach((item) => {
    item.disabled = !runtimeInfo.repairAvailable;
  });
  setDot("updates", runtimeInfo.activeRelease === runtimeInfo.desktopRelease ? "ok" : "warn");
  $("rollback-notice").hidden = runtimeInfo.rollbackAvailable;
}

async function loadRuntimeInfo() {
  try {
    runtimeInfo = await invoke("runtime_info");
    renderRuntime();
  } catch (error) {
    toast(String(error), true);
  }
}

function requestSnapshot() {
  clearTimeout(snapshotTimer);
  snapshotTimer = setTimeout(() => {
    invoke("control_snapshot", { id: nextId("snapshot") }).catch((error) => toast(String(error), true));
  }, 100);
}

function handleLocaldEvent(event) {
  if (event.event === "control.snapshot") {
    snapshot = event;
    state = event.state;
    if (!sharingChoice) sharingChoice = snapshot.sharing?.mode || "this_computer";
    fillConfiguration();
    render();
  }
  if (event.event === "config.applied") {
    if (snapshot) snapshot.operator = event.operator;
    fillConfiguration();
    render();
    document.querySelectorAll("[data-save]").forEach((button) => {
      button.disabled = false;
      button.textContent = button.closest('[data-page="ai"]')
        ? "Validate & apply"
        : button.closest('[data-page="integrations"]') ? "Save integrations" : "Save channels";
    });
    toast("Configuration saved and backend health checks passed.");
    requestSnapshot();
  }
  if (event.event === "error") {
    sharingBusy = false;
    document.querySelectorAll("[data-save]").forEach((button) => {
      button.disabled = false;
    });
    toast(event.message || "Local operation failed", true);
    requestSnapshot();
  }
  if (event.event === "sharing.progress") {
    if (event.sharing && snapshot) snapshot.sharing = event.sharing;
    render();
  }
  if (event.event === "sharing.changed") {
    sharingBusy = false;
    if (event.sharing && snapshot) snapshot.sharing = event.sharing;
    sharingChoice = event.sharing?.mode || "this_computer";
    render();
    toast(event.sharing?.mode === "this_computer" ? "Sharing stopped. Lemma is private to this computer." : "Sharing is active.");
    requestSnapshot();
  }
  if (event.event === "sharing.preflight") requestSnapshot();
  if (event.event === "agent-host.status" && event.agent_host && snapshot) {
    snapshot.agent_host = event.agent_host;
    renderAgentHost(snapshot.agent_host);
  }
  if (["status", "state", "ready", "phase", "done", "agent-host.status"].includes(event.event)) {
    requestSnapshot();
    if (event.event === "ready") loadRuntimeInfo();
  }
}

configureInteractionHandlers();
document.addEventListener("click", (event) => {
  const row = event.target.closest("[data-summary-page]");
  if (row?.dataset.summaryPage) setPage(row.dataset.summaryPage);
});
listen("lemma:control-page", (page) => {
  if (typeof page === "string") setPage(page);
});
listen("lemma:locald-event", handleLocaldEvent);
setPage(titles[window.__LEMMA_CONTROL_PAGE__] ? window.__LEMMA_CONTROL_PAGE__ : "overview");
requestSnapshot();
loadRuntimeInfo();
