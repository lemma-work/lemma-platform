// Local settings: navigation, wiring, and start-up. Each page's behaviour
// lives in `control/`, one module per concern.

import {
  $,
  IS_WINDOWS,
  LOCAL_MODE,
  LOCAL_PAGES,
  copyText,
  forThisDevice,
  friendlyError,
  invoke,
  listen,
  nextId,
  pendingSaves,
  store,
  toast,
} from "./control/core.js";
import { startLogPolling, stopLogPolling, wireLogControls } from "./control/logs.js";
import { loadAppUpdate, loadRuntimeInfo } from "./control/updates.js";
import { closeLocalSettings, runDesktopAction } from "./control/actions.js";
import {
  applyProviderPreset,
  clearDiscoveredModels,
  discoverModels,
  fillConfiguration,
  labelSecretButton,
  markDirty,
  saveConfiguration,
  setSectionError,
} from "./control/config.js";
import { loadTelemetry } from "./control/overview.js";
import {
  disableSharing,
  enableLanSharing,
  enablePublicSharing,
  renderSharing,
  selectSharingChoice,
} from "./control/sharing.js";
import {
  handleLocaldEvent,
  requestSnapshot,
  scheduleSnapshotRetry,
  showSnapshotUnavailable,
} from "./control/events.js";

const titles = {
  computer: ["This computer", "Installed agents and the connection to your workspace."],
  overview: ["Overview", "Health, attention, and exposure at a glance."],
  ai: ["AI provider", "Choose and validate the system model profile used by local agents."],
  sharing: ["Sharing", "Keep Lemma private, use it on trusted Wi-Fi, or create an intentional public link."],
  integrations: ["Integrations", "Configure service connections without mixing them with login or channel credentials."],
  channels: ["Channels", "Make agents reachable through only the receivers you explicitly enable."],
  runtime: ["Runtime", "Application health, lifecycle controls, and private dependency status."],
  updates: ["Updates", "Exact release matching, verified packs, and safe repair boundaries."],
  recovery: ["Recovery", "Repair a broken installation or explicitly erase local Lemma and set up again."],
  diagnostics: ["Diagnostics", "Local paths, canonical origins, logs, and non-destructive repair."],
};

function setPage(page) {
  if (!titles[page]) return;
  if (!LOCAL_MODE && LOCAL_PAGES.has(page)) page = "computer";
  document.querySelectorAll(".nav-item").forEach((button) => {
    const current = button.dataset.page === page;
    button.classList.toggle("active", current);
    // Which page you are on was carried by a background colour and nothing
    // else, so a screen reader read eleven identical navigation buttons.
    if (current) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.dataset.page === page);
  });
  $("page-title").textContent = titles[page][0];
  $("page-subtitle").textContent = titles[page][1];
  document.querySelector(".content").scrollTo({ top: 0, behavior: "instant" });
  // Only poll while the logs are actually on screen.
  if (page === "diagnostics") {
    startLogPolling();
    loadTelemetry();
  } else {
    stopLogPolling();
  }
}

function configureInteractionHandlers() {
  document.querySelectorAll('[data-action="reset-local-data"]').forEach((button) => { button.disabled = !LOCAL_MODE; });
  document.querySelectorAll(".nav-item").forEach((button) => {
    if (!LOCAL_MODE && LOCAL_PAGES.has(button.dataset.page)) {
      button.disabled = true;
      button.title = "Available when using Local Lemma";
    }
  });
  $("deployment-description").textContent = LOCAL_MODE
    ? "Local Lemma runs its services and stores application data on this computer. Configured LLMs, connectors, and online features can send requested prompts, tool results, and payloads externally."
    : window.__LEMMA_DESKTOP__?.mode === "undecided"
      ? "Choose Lemma Cloud or Local Lemma when you return to setup. Cloud stores workspace data online; Local Lemma stores application data and runs services on this computer. Configured providers and connectors can communicate externally in either mode."
      : "Your workspace data and orchestration live in Lemma Cloud. Installed coding agents run on this computer. Their requested results are sent to your cloud workspace.";
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setPage(button.dataset.page));
  });
  $("back-to-lemma").addEventListener("click", closeLocalSettings);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    // Escape inside a field belongs to the field. Typing a password, pressing
    // Escape to dismiss the browser's own suggestion list, and having the
    // whole settings window close instead is not a shortcut anybody asked
    // for. The first Escape leaves the control; a second one closes.
    if (isEditingControl(event.target)) {
      event.target.blur();
      return;
    }
    closeLocalSettings();
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
    const discard = document.createElement("button");
    discard.className = "btn";
    discard.textContent = "Discard changes";
    discard.addEventListener("click", () => {
      const page = button.closest(".config-page");
      if ([...pendingSaves.values()].some((pending) => pending.page === page)) return;
      page.classList.remove("dirty");
      setSectionError(page, "");
      fillConfiguration();
    });
    button.parentElement.append(discard);
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
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", () => applyProviderPreset(button.dataset.preset));
  });
  $("ai-discover").addEventListener("click", discoverModels);
  $("ai-base").addEventListener("input", () => {
    // The listed models belong to the endpoint they came from. Once that
    // changes they are someone else's models, and offering them as a choice
    // is how a default that the provider has never heard of gets saved.
    clearDiscoveredModels();
  });
  for (const id of ["ai-protocol", "ai-key", "ai-private-network"]) {
    $(id).addEventListener("change", clearDiscoveredModels);
  }
  document.querySelectorAll("[data-sharing-mode]").forEach((button) => {
    button.addEventListener("click", () => selectSharingChoice(button.dataset.sharingMode));
  });
  document.querySelectorAll("[data-provider]").forEach((button) => {
    button.addEventListener("click", () => {
      store.sharingProvider = button.dataset.provider;
      document.querySelectorAll("[data-provider]").forEach((candidate) => {
        candidate.classList.toggle("active", candidate.dataset.provider === store.sharingProvider);
      });
      renderSharing(store.snapshot?.sharing);
      invoke("sharing_action", {
        action: "preflight",
        id: nextId("sharing-preflight"),
        payload: { provider: store.sharingProvider },
      }).catch((error) => toast(friendlyError(error), true));
    });
  });
  $("cloudflare-setup").addEventListener("change", () => {
    store.cloudflareSetupChoice = $("cloudflare-setup").value;
    renderSharing(store.snapshot?.sharing);
  });
  $("sharing-enable-lan").addEventListener("click", enableLanSharing);
  $("sharing-enable-public").addEventListener("click", enablePublicSharing);
  $("sharing-disable").addEventListener("click", disableSharing);
}

// Whether an element is a control the user is editing.
function isEditingControl(target) {
  if (!target || typeof target.tagName !== "string") return false;
  if (target.isContentEditable) return true;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

configureInteractionHandlers();
wireLogControls();

// The webview is destroyed rather than navigated when Local settings closes,
// but a stray interval that outlives the page would keep waking the daemon.
window.addEventListener("pagehide", stopLogPolling);
document.addEventListener("click", (event) => {
  const row = event.target.closest("[data-summary-page]");
  if (row?.dataset.summaryPage) setPage(row.dataset.summaryPage);
});
listen("lemma:control-page", (page) => {
  if (typeof page === "string") setPage(page);
});
listen("lemma:locald-event", handleLocaldEvent);
listen("lemma:locald-disconnected", () => {
  showSnapshotUnavailable("The local service manager disconnected. Reconnecting; your drafts are preserved.");
  scheduleSnapshotRetry();
});
// Static copy in control.html, rewritten wholesale rather than kept as a list
// of ids someone has to remember to extend. Text nodes only -- attributes and
// element structure are untouched.
if (IS_WINDOWS) {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      node.parentNode && /^(SCRIPT|STYLE)$/.test(node.parentNode.nodeName)
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const spoken = forThisDevice(node.nodeValue);
    if (spoken !== node.nodeValue) node.nodeValue = spoken;
  }
}
setPage(titles[window.__LEMMA_CONTROL_PAGE__] ? window.__LEMMA_CONTROL_PAGE__ : "overview");
requestSnapshot();
loadRuntimeInfo();
loadAppUpdate();
