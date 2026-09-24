// Sharing: who can reach this installation, and the tunnel that allows it.

import {
  $,
  confirmAction,
  copyText,
  escapeHtml,
  friendlyError,
  invoke,
  nextId,
  store,
  toast,
} from "./core.js";

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

/** Who may create an account once the installation is shared.
 *
 *  The sentence locald confirms against is the source of truth (it is what
 *  `public_confirmation` carries); these are the same sentences for the places
 *  this page has to say it before a snapshot has arrived. Invite-only is the
 *  default because an unset preference means exactly that on the daemon side. */
export function joinPolicyCopy(whoCanJoin, mode) {
  const open = whoCanJoin === "open";
  if (mode === "public") {
    return open
      ? "Anyone with this link can create an account and use this Lemma installation."
      : "Anyone with this link can reach this Lemma's sign-in page. Only people you invite can create an account.";
  }
  return open
    ? "Anyone on this network can create an account."
    : "Only people you invite can create an account.";
}

export function selectSharingChoice(mode) {
  store.sharingChoice = mode;
  document.querySelectorAll("[data-sharing-mode]").forEach((button) => {
    const active = button.dataset.sharingMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", String(active));
  });
  $("lan-config").hidden = mode !== "local_network";
  $("public-config").hidden = mode !== "public";
}

export function renderSharing(sharing = {}) {
  if (!store.sharingChoice) store.sharingChoice = sharing.mode || "this_computer";
  selectSharingChoice(store.sharingChoice);
  const actualMode = sharing.mode || "this_computer";
  const stackReady = Boolean(store.snapshot?.state?.ready && store.snapshot?.state?.running);
  const transition = store.sharingBusy
    || Boolean(sharing.transition_running)
    || !["ready", "error"].includes(sharing.phase || "ready");
  store.sharingBusy = transition;
  $("sharing-phase").textContent = transition ? String(sharing.phase || "working").replaceAll("_", " ") : sharing.phase === "error" ? "attention" : "ready";
  $("sharing-phase").className = `status ${sharing.phase === "error" ? "bad" : actualMode === "this_computer" ? "ok" : "warn"}`;
  $("sharing-current-mode").textContent = actualMode === "this_computer" ? "Private" : actualMode === "public" ? "Internet" : "Wi-Fi";
  $("sharing-current-copy").textContent = exposureCopy(actualMode);
  $("sharing-url").textContent = sharing.canonical_url || store.snapshot?.state?.url || "—";
  $("sharing-disable").hidden = actualMode === "this_computer";
  $("sharing-disable").disabled = transition;
  $("sharing-enable-lan").disabled = transition || !stackReady;
  $("sharing-enable-public").disabled = transition || !stackReady;
  const warnings = stackReady
    ? (sharing.warnings || [])
    : ["Start Lemma and wait until the local stack is healthy before enabling sharing.", ...(sharing.warnings || [])];
  $("sharing-warnings").innerHTML = warnings.map((warning) => `<div class="warning-box">${escapeHtml(warning)}</div>`).join("");

  const whoCanJoin = sharing.who_can_join || sharing.preferences?.who_can_join || "invite_only";
  $("lan-join-copy").textContent = joinPolicyCopy(whoCanJoin, "local_network");
  $("public-join-title").textContent = whoCanJoin === "open" ? "Open signup is enabled." : "Signup is invite-only.";
  $("public-join-copy").textContent = sharing.public_confirmation || joinPolicyCopy(whoCanJoin, "public");

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
  // Not innerHTML. This window can reinstall Lemma and write credentials, so
  // markup arriving over the daemon's event stream must not become live DOM
  // here. An SVG loaded through <img> cannot run script, and img-src already
  // permits data: URIs.
  const qrImage = $("sharing-qr-image");
  qrImage.replaceChildren();
  if (qrVisible) {
    const img = document.createElement("img");
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(sharing.qr_svg)}`;
    img.alt = "QR code for this computer's Lemma address";
    img.decoding = "async";
    qrImage.appendChild(img);
  }

  $("cloudflare-fields").hidden = store.sharingProvider !== "cloudflare";
  const readiness = sharing.provider_readiness?.[store.sharingProvider] || {};
  renderProviderReadiness(readiness);
  if (store.sharingProvider === "cloudflare") {
    const setup = store.cloudflareSetupChoice
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
  const providerName = store.sharingProvider === "cloudflare" ? "cloudflared" : "ngrok";
  const ready = readiness.installed && readiness.authenticated;
  const stackReady = Boolean(store.snapshot?.state?.ready && store.snapshot?.state?.running);
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
  $("sharing-enable-public").disabled = store.sharingBusy || !ready || !stackReady;
}

export async function enableLanSharing() {
  const selectedInterface = $("sharing-interface").value;
  if (!selectedInterface) {
    toast("Choose a private IPv4 network interface.", true);
    return;
  }
  store.sharingBusy = true;
  renderSharing(store.snapshot?.sharing);
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
    store.sharingBusy = false;
    toast(friendlyError(error), true);
  }
}

export async function enablePublicSharing() {
  try {
    const sharing = store.snapshot?.sharing || {};
    const whoCanJoin = sharing.who_can_join || sharing.preferences?.who_can_join || "invite_only";
    const joining = sharing.public_confirmation || joinPolicyCopy(whoCanJoin, "public");
    if (await confirmAction(
      "Create a public link?",
      `${joining} The workspace, auth, API, files, chat, tools, streaming, and webhook callbacks will be reachable from the internet.`,
      "I understand · create link",
    )) await activatePublicSharing();
  } catch (error) {
    toast(friendlyError(error), true);
  }
}

async function activatePublicSharing() {
  const payload = {
    mode: "public",
    provider: store.sharingProvider,
    public_warning_confirmed: true,
  };
  if (store.sharingProvider === "cloudflare") {
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
  store.sharingBusy = true;
  renderSharing(store.snapshot?.sharing);
  try {
    await invoke("sharing_action", {
      action: "enable",
      id: nextId("sharing-enable-public"),
      payload,
    });
    toast(`Starting ${store.sharingProvider === "cloudflare" ? "Cloudflare" : "ngrok"} and validating the public origin…`);
  } catch (error) {
    store.sharingBusy = false;
    toast(friendlyError(error), true);
  }
}

/** Change who may create an account. Applied immediately if sharing is on. */
export async function setWhoCanJoin(whoCanJoin) {
  try {
    await invoke("sharing_action", {
      action: "access",
      id: nextId("sharing-access"),
      payload: { who_can_join: whoCanJoin },
    });
  } catch (error) {
    toast(friendlyError(error), true);
  }
}

export async function disableSharing() {
  store.sharingBusy = true;
  renderSharing(store.snapshot?.sharing);
  try {
    await invoke("sharing_action", {
      action: "disable",
      id: nextId("sharing-disable"),
    });
    toast("Restoring This computer mode…");
  } catch (error) {
    store.sharingBusy = false;
    toast(friendlyError(error), true);
  }
}
