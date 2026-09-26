// Local settings: navigation, wiring, and start-up. Each page's behaviour
// lives in `control/`, one module per concern.

import {
  $,
  IS_WINDOWS,
  LOCAL_MODE,
  LOCAL_PAGES,
  copyText,
  forThisDevice,
  listen,
} from "./control/core.js";
import { startLogPolling, stopLogPolling, wireLogControls } from "./control/logs.js";
import { loadAppUpdate, loadRuntimeInfo } from "./control/updates.js";
import { closeLocalSettings, runDesktopAction } from "./control/actions.js";
import { loadTelemetry } from "./control/overview.js";
import { disableSharing } from "./control/sharing.js";
import {
  handleLocaldEvent,
  requestSnapshot,
  scheduleSnapshotRetry,
  showSnapshotUnavailable,
} from "./control/events.js";

// What is left here is what has to work when the workspace does not. The AI
// provider, sharing, integrations, channels and updates moved to the
// workspace's own Settings, under This Mac, where the rest of Lemma's settings
// already were; the menu opens them there whenever the local workspace is up.
const titles = {
  computer: ["This computer", LOCAL_MODE
    ? "Installed agents and the connection to your workspace."
    : "Installed agents, the connection to your workspace, and this app's version."],
  overview: ["Overview", "Health, what is exposed, and this app's version."],
  recovery: ["Recovery", "Repair a broken installation or explicitly erase local Lemma and set up again."],
  diagnostics: ["Diagnostics", "Local paths, canonical origins, logs, and non-destructive repair."],
};

/* Where a button or a menu item can send this window: a page, or a place on
 * one. `updates` is the update panel, which is on Overview locally and on
 * This computer in cloud mode (moved there below) -- Check for Updates…
 * opens it, and it checks again on the way in. `sharing` is the Return to
 * This computer button; `services` is Overview's health list. */
function goTo(target) {
  if (target === "updates") {
    setPage(LOCAL_MODE ? "overview" : "computer");
    reveal($("app-update-panel"));
    loadAppUpdate();
    return;
  }
  if (target === "sharing") {
    setPage("overview");
    const stop = $("sharing-disable");
    reveal(stop.hidden ? $("overview-exposure") : stop);
    return;
  }
  if (target === "services") {
    setPage("overview");
    reveal($("overview-services"));
    return;
  }
  setPage(target);
}

function reveal(element) {
  if (!element) return;
  element.scrollIntoView({ block: "center", behavior: "instant" });
  if (typeof element.focus === "function" && element.tagName === "BUTTON" && !element.disabled) {
    element.focus({ preventScroll: true });
  }
}

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
  // Cloud mode has no local services to start or restart.
  document.querySelectorAll('[data-action="start"], [data-action="restart"]').forEach((button) => {
    button.disabled = !LOCAL_MODE;
    if (!LOCAL_MODE) button.title = "Available when using Local Lemma";
  });
  if (!LOCAL_MODE) {
    // The update panel lives on Overview, which cloud mode cannot open, and
    // This Mac -- the workspace's other update control -- is local-only too.
    document.querySelector('.page[data-page="computer"]').appendChild($("app-update-panel"));
  }
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
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => runDesktopAction(button));
  });
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", () => copyText($(button.dataset.copyTarget).textContent));
  });
  $("attention-action").addEventListener("click", () => {
    goTo($("attention-action").dataset.page || "overview");
  });
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
  if (row?.dataset.summaryPage) goTo(row.dataset.summaryPage);
  const jump = event.target.closest("[data-goto]");
  if (jump?.dataset.goto) goTo(jump.dataset.goto);
});
listen("lemma:control-page", (page) => {
  if (typeof page === "string") goTo(page);
});
listen("lemma:locald-event", handleLocaldEvent);
listen("lemma:locald-disconnected", () => {
  showSnapshotUnavailable("The local service manager disconnected. Reconnecting.");
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
const firstPage = window.__LEMMA_CONTROL_PAGE__;
requestSnapshot();
loadRuntimeInfo();
if (firstPage === "updates") {
  goTo("updates");
} else {
  setPage(titles[firstPage] ? firstPage : "overview");
  loadAppUpdate();
}
