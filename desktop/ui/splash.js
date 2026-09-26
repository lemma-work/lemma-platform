import { deriveScreen, diagnosticSourceForState } from "./screen-state.mjs";

// ---- bridge: Tauri when available, scripted demo otherwise ------------
(function () {
  const tauri = window.__TAURI__;
  if (tauri) {
    const invoke = (cmd, args) => tauri.core.invoke(cmd, args);
    window.lemmaDesktop = {
      start: () => invoke("start"),
      stop: (includeInfra = false) => invoke("stop", { includeInfra }),
      restart: () => invoke("restart"),
      prepareRuntime: () => invoke("prepare_runtime"),
      openApp: () => invoke("open_app"),
      openAuth: (mode = "signin") => invoke("login", { mode }),
      openLogs: () => invoke("open_logs"),
      diagnosticLogs: (source, cursor = null) =>
        invoke("diagnostic_logs", { source, cursor }),
      chooseConnectionMode: () => invoke("choose_connection_mode"),
      setConnectionMode: (mode) => invoke("set_connection_mode", { mode }),
      getState: () => invoke("get_state"),
      recoveryOptions: () => invoke("local_recovery_options"),
      openRecovery: () => invoke("open_control_center", { page: "recovery" }),
      resetLocalData: () => invoke("reset_local_data"),
      resetFullReinstall: () => invoke("reset_full_reinstall"),
      onLog: (cb) => { tauri.event.listen("lemma:log", (e) => cb(e.payload)); },
      onState: (cb) => { tauri.event.listen("lemma:state", (e) => cb(e.payload)); },
    };
    return;
  }
  const listeners = { log: [], state: [] };
  const seq = ["download", "check", "infra", "workspace", "migrations", "backend", "frontend", "verify", "ready"];
  let i = 0, timer = null;
  const push = (s) => listeners.state.forEach((cb) => cb(s));
  const step = () => {
    const key = seq[i];
    push({
      status: key, phase: key, phaseKey: key,
      progress: Math.round(((i + 1) / seq.length) * 100),
      etaSeconds: (seq.length - i) * 7, setup: true,
      downloadedBytes: key === "download" ? 104857600 : null,
      totalBytes: key === "download" ? 524288000 : null,
      error: false, ready: key === "ready", running: true,
      mode: "local", url: "", apiUrl: "",
    });
    listeners.log.forEach((cb) => cb(`[demo] phase: ${key}`));
    if (i < seq.length - 1) { i += 1; timer = setTimeout(step, 7000); }
  };
  window.lemmaDesktop = {
    start: () => { clearTimeout(timer); i = 0; step(); },
    stop: () => clearTimeout(timer),
    restart: () => { clearTimeout(timer); i = 0; step(); },
    prepareRuntime: () => console.log("[demo] prepare Windows runtime"),
    openApp: () => console.log("[demo] open app"),
    openAuth: (mode = "signin") => console.log("[demo] open auth", mode),
    openLogs: () => console.log("[demo] open logs"),
    diagnosticLogs: (source) => Promise.resolve({
      source: source || "events",
      entries: `[demo] ${source || "events"} log is available here`,
      nextCursor: "v1:demo:1",
      sources: [
        { id: "events", label: "Events" },
        { id: "backend", label: "Backend" },
        { id: "frontend", label: "Frontend" },
      ],
    }),
    chooseConnectionMode: () => console.log("[demo] switch mode"),
    setConnectionMode: (mode) => { console.log("[demo] mode:", mode); if (mode === "local") window.lemmaDesktop.start(); },
    getState: () => Promise.resolve({ status: "waiting", phaseKey: "boot", progress: 2, ready: false, running: false, error: false, setup: true, mode: "undecided" }),
    recoveryOptions: () => Promise.resolve({
      dataResetAvailable: true,
      fullReinstallAvailable: true,
      installedRuntimeRelease: "0.0.0-demo",
      dataDiskAllocatedBytes: 0,
    }),
    resetLocalData: () => console.log("[demo] reset local data"),
    resetFullReinstall: () => console.log("[demo] full reinstall"),
    onLog: (cb) => listeners.log.push(cb),
    onState: (cb) => listeners.state.push(cb),
  };
})();

// ---- the script: poetic line + honest mono subtitle per real phase ----
const IS_WINDOWS = /Windows/i.test(navigator.userAgent);
document.getElementById("local-network-permission").hidden = IS_WINDOWS;
const DEVICE = IS_WINDOWS ? "PC" : "Mac";
/** Any sentence about the machine, said the way this machine's owner would. */
function forThisDevice(text) {
  if (!IS_WINDOWS) return text;
  return text.replace(/\bthis Mac\b/g, "this PC").replace(/\bthis mac\b/g, "this PC");
}
const VOICE = {
  boot:       [`A new kind of workspace is arriving on this ${DEVICE}.`, "starting"],
  download:   ["Bringing the runtime here.", "downloading runtime"],
  check:      ["Preparing the ground.", "toolchain & configuration"],
  infra:      ["Giving it memory.", "database & cache"],
  workspace:  ["Building a safe room for agents to work in.", "agent sandbox"],
  migrations: ["Laying down structure.", "schemas & migrations"],
  backend:    ["Waking the services.", "core services"],
  frontend:   ["Drawing the interface.", "app"],
  verify:     ["Final checks.", "verifying"],
  ready:      ["It's ready.", "all systems up · your account and data stay on this mac"],
  stopping:   ["Winding down.", "stopping services"],
  stopped:    ["Asleep.", "services stopped"],
};
const READY_LINE = "It's ready. Opening Lemma.";
const openIntent = new URLSearchParams(location.search).get("intent");
// This window was opened to watch something stop. The daemon can still emit
// a snapshot taken before the stop began, and acting on its `ready` would
// navigate the user into a workspace whose services are going away.
const isShuttingDown = openIntent === "quit" || openIntent === "stop";

const scene = document.getElementById("scene");
const lineEl = document.getElementById("line");
const truth = document.getElementById("truth");
const whisper = document.getElementById("whisper");
const progressEl = document.querySelector(".progress");
const bar = document.getElementById("bar");
const runinfo = document.getElementById("runinfo");
const openBtn = document.getElementById("open-app");
const errwrap = document.getElementById("errwrap");
const errdetail = document.getElementById("errdetail");
const retryBtn = document.getElementById("retry");
const prepareWindowsBtn = document.getElementById("prepare-windows");
const resetDataBtn = document.getElementById("reset-data");
const fullReinstallBtn = document.getElementById("full-reinstall");
const keepDataLink = document.getElementById("keep-data");
const errorRecoveryBtn = document.getElementById("error-recovery");
const errnote = document.getElementById("errnote");
const cloudSetupError = document.getElementById("cloud-setup-error");
// What the reset button is called on the screen showing it, so the label it
// goes back to after a reset attempt is that screen's, not a hard-coded one.
let resetLabel = "Reset local data";
const operationStatus = document.getElementById("operation-status");
const operationDetail = document.getElementById("operation-detail");
const operationMeta = document.getElementById("operation-meta");

// "this Mac" is never right on Windows, so text nodes are rewritten wholesale
// rather than by a list of ids someone has to remember to extend. Attributes
// and element structure are untouched.
function speakTheRightDevice(root) {
  if (!IS_WINDOWS) return;
  // SCRIPT and STYLE hold text nodes too. Rewriting them changes nothing
  // anyone sees, but leaves a DOM whose source no longer matches the file.
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
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
speakTheRightDevice(document.body);
// The lines written from JS never passed through that walk, so the phase
// subtitles kept saying "mac" on a PC. Rewritten once here rather than at
// every `say()`, so the table stays readable as prose.
for (const key of Object.keys(VOICE)) VOICE[key] = VOICE[key].map(forThisDevice);

let shownLine = "";
let queuedLine = null;
let saying = false;
function say(rawText) {
  const text = forThisDevice(rawText);
  if (saying) { queuedLine = text; return; }
  if (text === shownLine) return;
  saying = true;
  lineEl.classList.add("out");
  setTimeout(() => {
    lineEl.textContent = text;
    shownLine = text;
    lineEl.classList.remove("out");
    lineEl.classList.add("pre");
    void lineEl.offsetHeight;
    lineEl.classList.remove("pre");
    setTimeout(() => {
      saying = false;
      if (queuedLine !== null) {
        const next = queuedLine;
        queuedLine = null;
        say(next);
      }
    }, 900);
  }, 440);
}

let sawSetup = false;
let lastPhase = "";
let lastState = null;
let readyOpenTimer = null;
let startRequestPending = false;
function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = value >= 100 || unit === 0 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units[unit]}`;
}
function operationSummary(s) {
  const parts = [];
  if (Number.isFinite(s.progress)) parts.push(`${Math.min(100, Math.max(0, s.progress))}%`);
  if (Number.isFinite(s.downloadedBytes) && Number.isFinite(s.totalBytes) && s.totalBytes > 0) {
    parts.push(`${formatBytes(s.downloadedBytes)} of ${formatBytes(s.totalBytes)}`);
  }
  if (Number.isFinite(s.throughputBytesPerSecond) && s.throughputBytesPerSecond > 0) {
    parts.push(`${formatBytes(s.throughputBytesPerSecond)}/s`);
  }
  if (s.etaSeconds) parts.push(`about ${formatEta(s.etaSeconds)} remaining`);
  return parts.join(" · ");
}
function operationDescription(s, fallback) {
  const status = String(s.status || "").trim();
  const phase = String(s.phase || "").trim();
  if (!status || status === phase) return fallback;
  const prefix = `${phase}: `;
  return status.startsWith(prefix) ? status.slice(prefix.length) : status;
}
function hideOperationStatus() {
  operationStatus.hidden = true;
  operationDetail.textContent = "";
  operationMeta.textContent = "";
}
function showOperationStatus(title, detail, meta) {
  say(title);
  operationDetail.textContent = detail;
  operationMeta.textContent = meta;
  operationStatus.hidden = false;
}
function clearPrimaryBusy() {
  startRequestPending = false;
  openBtn.disabled = false;
  openBtn.classList.remove("busy");
  openBtn.removeAttribute("aria-busy");
}
function scheduleReadyOpen() {
  if (isShuttingDown) return;
  clearTimeout(readyOpenTimer);
  readyOpenTimer = setTimeout(async () => {
    if (!lastState?.ready || lastState.error) return;
    try {
      await window.lemmaDesktop.openApp();
    } catch (error) {
      clearPrimaryBusy();
      openBtn.hidden = false;
      openBtn.textContent = "Open Lemma";
      appendLog(`ERROR ${String(error)}`);
    }
  }, 650);
}
function renderState(s) {
  if (!s) return;
  if (openIntent === "quit") {
    clearTimeout(readyOpenTimer);
    stopWhispers();
    hideConnectionChoice();
    switchModeBtn.hidden = true;
    openBtn.hidden = true;
    prepareWindowsBtn.hidden = true;
    showRecoveryButtons(false, false);
    say(s.error ? "Lemma could not finish stopping." : "Stopping Lemma.");
    truth.textContent = s.phaseKey === "stopping" ? s.status : "Waiting for local work to stop safely";
    errwrap.hidden = !s.error;
    errdetail.textContent = s.error ? s.status : "";
    retryBtn.textContent = "Retry shutdown";
    retryBtn.hidden = !s.error;
    progressEl.classList.toggle("active", !s.error);
    return;
  }
  // `deriveScreen` may not decline a state. This function is the only thing
  // that draws the screen and the only thing that schedules the move to the
  // workspace, so a state nothing handles leaves the bare static logo up for
  // good -- which is exactly what a stuck first run looked like. A state with
  // no phase is still a state, and `lastPhase` is how it keeps one.
  const screen = deriveScreen(s, { lastPhase, sawSetup, isShuttingDown });

  if (screen.screen === "choosing") {
    showChooser();
    return;
  }
  lastState = s;
  switchModeBtn.hidden = s.mode !== "local";
  if (s.running || s.ready) hideConnectionChoice();
  sawSetup = screen.sawSetup;
  lastPhase = screen.phaseKey;

  if (screen.screen === "error") {
    if (logPanel.hidden) activeLogSource = screen.logSource;
    clearTimeout(readyOpenTimer);
    clearPrimaryBusy();
    hideOperationStatus();
    progressEl.classList.remove("active");
    window.__orb?.stall();
    scene.classList.add("stalled");
    scene.classList.remove("awake");
    say(screen.headline);
    truth.textContent = screen.windowsSetup
      ? "one-time Windows setup · no Docker Desktop or Ubuntu"
      : screen.windowsRestart
        ? "restart Windows, then reopen Lemma"
        : "";
    errdetail.textContent = screen.errorDetail;
    errnote.textContent = screen.note;
    errnote.hidden = !screen.note;
    errwrap.hidden = false;
    keepDataLink.hidden = !screen.keepDataUrl;
    if (screen.keepDataUrl) keepDataLink.href = screen.keepDataUrl;
    errorRecoveryBtn.hidden = !screen.showRecovery;
    resetLabel = screen.resetLabel;
    if (!resetDataBtn.disabled) resetDataBtn.textContent = resetLabel;
    // Keeping the data is the filled button when it is offered; erasing it
    // steps down to an outlined one in the error colour.
    resetDataBtn.classList.toggle("danger", Boolean(screen.keepDataUrl));
    prepareWindowsBtn.hidden = !screen.showPrepareWindows;
    prepareWindowsBtn.disabled = false;
    prepareWindowsBtn.textContent = "Set up Windows runtime";
    showRecoveryButtons(screen.showResetData, screen.showFullReinstall && !screen.keepDataUrl);
    retryBtn.hidden = !screen.showRetry;
    openBtn.hidden = true;
    whisper.classList.remove("on");
    return;
  }
  if (scene.classList.contains("stalled")) window.__orb?.resume();
  scene.classList.remove("stalled");
  errwrap.hidden = true;
  errnote.hidden = true;
  keepDataLink.hidden = true;
  errorRecoveryBtn.hidden = true;
  prepareWindowsBtn.hidden = true;
  retryBtn.hidden = false;
  showRecoveryButtons(false, false);

  const voice = VOICE[screen.phaseKey] || VOICE.boot;
  if (screen.screen === "stopping") {
    clearPrimaryBusy();
    say(VOICE.stopping[0]);
    truth.textContent = VOICE.stopping[1];
    openBtn.hidden = true;
    whisper.classList.remove("on");
    stopWhispers();
    progressEl.classList.add("active");
    bar.style.width = "100%";
    return;
  }
  if (screen.screen === "ready") {
    clearPrimaryBusy();
    hideOperationStatus();
    window.__orb?.awake();
    scene.classList.add("awake");
    say(READY_LINE);
    truth.textContent = VOICE.ready[1];
    primaryAction = screen.primaryAction;
    openBtn.textContent = "Open Lemma";
    openBtn.hidden = !screen.showOpen;
    whisper.classList.remove("on");
    stopWhispers();
    scheduleReadyOpen();
  } else if (screen.screen === "stopped") {
    clearTimeout(readyOpenTimer);
    clearPrimaryBusy();
    hideOperationStatus();
    window.__orb?.resume();
    scene.classList.add("awake");
    say(VOICE.stopped[0]);
    truth.textContent = VOICE.stopped[1];
    primaryAction = screen.primaryAction;
    openBtn.textContent = "Start Lemma";
    openBtn.hidden = !screen.showOpen;
    whisper.classList.remove("on");
  } else {
    if (scene.classList.contains("awake")) {
      scene.classList.remove("awake");
      window.__orb?.resume();
      startWhispers();
    }
    openBtn.hidden = true;
    openBtn.disabled = true;
    openBtn.classList.remove("busy");
    const title = String(s.phase || "").trim() || voice[0];
    showOperationStatus(
      title,
      operationDescription(s, voice[1]),
      operationSummary(s),
    );
    truth.textContent = s.setup
      ? "first setup in progress · keep lemma open"
      : "local services are starting";
  }

  bar.style.width = `${screen.progressWidth}%`;
  progressEl.classList.toggle("active", screen.progressActive);
  runinfo.textContent = screen.runInfo;
}
function formatEta(seconds) {
  return seconds >= 90 ? `${Math.round(seconds / 60)}m` : `${seconds}s`;
}

// ---- whispers: one quiet concept at a time -----------------------------
let whispers = [];
let whisperIndex = 0;
let whisperTimer = null;
function nextWhisper() {
  if (scene.classList.contains("choosing")) return;
  if (!whispers.length) return;
  whisper.classList.remove("on");
  setTimeout(() => {
    const c = whispers[whisperIndex % whispers.length];
    whisperIndex += 1;
    whisper.innerHTML = "";
    const term = document.createElement("span");
    term.className = "term";
    term.textContent = c.term;
    whisper.appendChild(term);
    whisper.appendChild(document.createTextNode(" — " + lowerFirst(c.oneLiner)));
    whisper.classList.add("on");
  }, 1400);
}
function lowerFirst(text) { return text.charAt(0).toLowerCase() + text.slice(1); }
function startWhispers() {
  clearInterval(whisperTimer);
  nextWhisper();
  whisperTimer = setInterval(nextWhisper, 14000);
}
function stopWhispers() { clearInterval(whisperTimer); }

fetch("concepts.gen.json")
  .then((r) => r.json())
  .then((data) => {
    whispers = data.tour || [];
    setTimeout(startWhispers, 5000);
  })
  .catch(() => {});

// ---- log ----------------------------------------------------------------
const logPanel = document.getElementById("log-panel");
const logEl = document.getElementById("log");
const logTabs = document.getElementById("log-tabs");
const logLines = [];
let activeLogSource = "events";
let diagnosticLog = "";
let diagnosticCursor = null;
let logRefreshTimer = null;
function renderLog() {
  const live = activeLogSource === "events" ? logLines.join("\n") : "";
  const sections = [diagnosticLog, live].filter(Boolean);
  logEl.textContent = sections.join("\n");
  logEl.scrollTop = logEl.scrollHeight;
}
// A `role="tablist"` whose children are plain buttons is worse than no role
// at all: it announces a tab list and then nothing inside it is a tab, the
// selected one is distinguishable only by colour, and arrow keys do nothing.
function renderLogTabs(sources) {
  // Choosing a source re-reads the log, which re-renders these buttons and
  // destroys the one that was focused -- dropping keyboard focus to the
  // document. Arrow-keying through the sources would move focus once and
  // then lose it.
  const hadFocus = logTabs.contains(document.activeElement);
  logTabs.innerHTML = "";
  for (const source of sources || []) {
    const button = document.createElement("button");
    button.type = "button";
    button.id = `log-tab-${source.id}`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", "log");
    const selected = source.id === activeLogSource;
    button.setAttribute("aria-selected", String(selected));
    // One stop for the whole set, as the tabs pattern requires: Tab reaches
    // the list, then Left/Right move within it.
    button.tabIndex = selected ? 0 : -1;
    button.textContent = source.label;
    button.classList.toggle("active", selected);
    button.addEventListener("click", () => selectLogSource(source.id));
    logTabs.appendChild(button);
    if (selected) {
      logEl.setAttribute("aria-labelledby", button.id);
      if (hadFocus) button.focus();
    }
  }
}

logTabs.addEventListener("keydown", (event) => {
  const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
  if (!step) return;
  const tabs = [...logTabs.querySelectorAll('[role="tab"]')];
  const current = tabs.indexOf(document.activeElement);
  if (current < 0) return;
  event.preventDefault();
  const next = tabs[(current + step + tabs.length) % tabs.length];
  next.focus();
  next.click();
});
async function refreshDiagnosticLog({ append = false } = {}) {
  try {
    const snapshot = await window.lemmaDesktop.diagnosticLogs(
      activeLogSource,
      append ? diagnosticCursor : null,
    );
    if (append && snapshot.entries && !snapshot.entries.startsWith("No ")) {
      diagnosticLog += snapshot.entries;
    } else if (!append) {
      diagnosticLog = snapshot.entries || "";
    }
    diagnosticCursor = snapshot.nextCursor;
    renderLogTabs(snapshot.sources);
  } catch (error) {
    diagnosticLog = `Could not read ${activeLogSource} log: ${String(error)}`;
  }
  if (!logPanel.hidden) renderLog();
}
async function selectLogSource(source) {
  activeLogSource = source;
  diagnosticCursor = null;
  diagnosticLog = "";
  await refreshDiagnosticLog();
}
function startLogRefresh() {
  clearInterval(logRefreshTimer);
  logRefreshTimer = setInterval(() => {
    if (!logPanel.hidden) refreshDiagnosticLog({ append: true });
  }, 1000);
}
function stopLogRefresh() {
  clearInterval(logRefreshTimer);
  logRefreshTimer = null;
}
function appendLog(line) {
  if (!line) return;
  logLines.push(line);
  if (logLines.length > 500) logLines.shift();
  if (!logPanel.hidden) renderLog();
}
function toggleLog() {
  logPanel.hidden = !logPanel.hidden;
  scene.classList.toggle("log-open", !logPanel.hidden);
  document.getElementById("toggle-log").textContent = logPanel.hidden ? "log" : "close log";
  if (!logPanel.hidden) {
    refreshDiagnosticLog();
    startLogRefresh();
  } else {
    stopLogRefresh();
  }
}
document.getElementById("toggle-log").addEventListener("click", toggleLog);
document.getElementById("error-logs").addEventListener("click", () => {
  activeLogSource = diagnosticSourceForState(lastState);
  diagnosticCursor = null;
  diagnosticLog = "";
  toggleLog();
});
document.getElementById("close-log").addEventListener("click", toggleLog);
document.getElementById("open-log-folder").addEventListener("click", () =>
  window.lemmaDesktop.openLogs().catch((error) => appendLog(`ERROR ${String(error)}`))
);
document.getElementById("copy-log").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(logEl.textContent || "");
  } catch (error) {
    appendLog(`ERROR Could not copy log: ${String(error)}`);
  }
});

// ---- actions ------------------------------------------------------------
let primaryAction = "open";
async function requestStart() {
  if (startRequestPending) return;
  startRequestPending = true;
  clearTimeout(readyOpenTimer);
  openBtn.disabled = true;
  openBtn.classList.add("busy");
  openBtn.setAttribute("aria-busy", "true");
  openBtn.textContent = "Starting Lemma";
  openBtn.hidden = true;
  showOperationStatus("Starting Lemma.", "Preparing local services", "6%");
  truth.textContent = "request accepted · keep lemma open";
  progressEl.classList.add("active");
  const currentProgress = Number.parseFloat(bar.style.width) || 0;
  bar.style.width = `${Math.max(6, currentProgress)}%`;
  try {
    await window.lemmaDesktop.start();
  } catch (error) {
    clearPrimaryBusy();
    hideOperationStatus();
    openBtn.hidden = false;
    openBtn.textContent = "Try Start again";
    appendLog(`ERROR ${String(error)}`);
  }
}
openBtn.addEventListener("click", async () => {
  if (primaryAction === "start") {
    await requestStart();
    return;
  }
  openBtn.disabled = true;
  openBtn.textContent = "Opening Lemma…";
  errwrap.hidden = true;
  try {
    await window.lemmaDesktop.openApp();
  } catch (error) {
    openBtn.disabled = false;
    openBtn.textContent = "Try opening Lemma again";
    say("Lemma did not open.");
    truth.textContent = "";
    errdetail.textContent = String(error || "Could not open Lemma.");
    // The services are up; it is the window that failed. The box's Try again
    // starts the stack, which is the wrong retry here -- and the button above
    // already says what the right one is, so the box keeps only the log.
    retryBtn.hidden = true;
    errnote.hidden = true;
    keepDataLink.hidden = true;
    errorRecoveryBtn.hidden = true;
    prepareWindowsBtn.hidden = true;
    showRecoveryButtons(false, false);
    errwrap.hidden = false;
  }
});
retryBtn.addEventListener("click", async () => {
  if (openIntent === "quit") {
    retryBtn.disabled = true;
    try {
      await window.lemmaDesktop.stop(true);
    } catch (error) {
      errdetail.textContent = String(error);
    } finally {
      retryBtn.disabled = false;
    }
    return;
  }
  errwrap.hidden = true;
  scene.classList.remove("stalled");
  say(VOICE.boot[0]);
  startWhispers();
  await requestStart();
});

/* --------------------------------------------------------- recovery ---
 * Shown only when the app has something to offer. `recoveryOptions` says
 * whether there is anything left on this Mac to reset, so a fresh install
 * that failed for some unrelated reason is not told to erase data it does
 * not have.
 *
 * Both buttons confirm natively, inside the command -- WKWebView's own
 * `window.confirm()` silently returns false, so a page-level dialog here
 * would read as "cancelled" every time.
 */
let recoveryOffered = false;
document.getElementById("open-recovery").addEventListener("click", () => {
  window.lemmaDesktop.openRecovery?.().catch((error) => {
    errdetail.textContent = String(error);
    // The box this writes into is hidden unless a failing state put it on
    // screen, and a Local settings window that will not open is not a
    // failing state. So the reason was written somewhere nobody could read.
    errwrap.hidden = false;
  });
});
async function showRecoveryButtons(offer, dataIsUnreadable) {
  if (!offer) {
    resetDataBtn.hidden = true;
    fullReinstallBtn.hidden = true;
    recoveryOffered = false;
    return;
  }
  if (recoveryOffered) return;
  recoveryOffered = true;
  let options = null;
  try {
    options = await window.lemmaDesktop.recoveryOptions();
  } catch {
    // Asking failed, so offer the tier that needs least to work.
    resetDataBtn.hidden = true;
    fullReinstallBtn.hidden = false;
    return;
  }
  resetDataBtn.hidden = !options.dataResetAvailable;
  fullReinstallBtn.hidden = !options.fullReinstallAvailable;
  // Resetting data is the recommended move for data this release cannot
  // read; starting over is always the last resort and never the primary.
  resetDataBtn.classList.toggle("dark", dataIsUnreadable && !resetDataBtn.classList.contains("danger"));
}
errorRecoveryBtn.addEventListener("click", () => {
  window.lemmaDesktop.openRecovery?.().catch((error) => {
    errdetail.textContent = String(error);
  });
});

resetDataBtn.addEventListener("click", async () => {
  resetDataBtn.disabled = true;
  resetDataBtn.textContent = "Resetting…";
  try {
    await window.lemmaDesktop.resetLocalData();
  } catch (error) {
    errdetail.textContent = String(error);
  } finally {
    resetDataBtn.disabled = false;
    resetDataBtn.textContent = resetLabel;
  }
});

fullReinstallBtn.addEventListener("click", async () => {
  fullReinstallBtn.disabled = true;
  fullReinstallBtn.textContent = "Starting over…";
  try {
    await window.lemmaDesktop.resetFullReinstall();
  } catch (error) {
    errdetail.textContent = String(error);
  } finally {
    fullReinstallBtn.disabled = false;
    fullReinstallBtn.textContent = "Start over";
  }
});
prepareWindowsBtn.addEventListener("click", () => {
  prepareWindowsBtn.disabled = true;
  prepareWindowsBtn.textContent = "Waiting for Windows…";
  errdetail.textContent = "Approve the Windows prompt. Lemma will continue here when setup finishes.";
  window.lemmaDesktop.prepareRuntime().catch((error) => {
    prepareWindowsBtn.disabled = false;
    prepareWindowsBtn.textContent = "Try Windows setup again";
    errdetail.textContent = String(error || "Windows runtime setup could not start.");
  });
});
const switchModeBtn = document.getElementById("switch-mode");
switchModeBtn.addEventListener("click", () => window.lemmaDesktop.chooseConnectionMode());

// ---- first-launch chooser ------------------------------------------------
const chooseEl = document.getElementById("choose");
const localConfirmEl = document.getElementById("local-confirm");
const localSetupError = document.getElementById("local-setup-error");
const confirmLocalBtn = document.getElementById("confirm-local");
function showChooser() {
  const entering = chooseEl.hidden;
  scene.classList.remove("choosing");
  scene.classList.add("welcoming");
  chooseEl.hidden = false;
  localConfirmEl.hidden = true;
  switchModeBtn.hidden = true;
  localSetupError.hidden = true;
  confirmLocalBtn.disabled = false;
  confirmLocalBtn.textContent = "Install local services";
  stopWhispers();
  whisper.classList.remove("on");
  hideOperationStatus();
  // The serif line is the welcome. It says nothing about what is installed on
  // this machine, because nothing has looked yet and a wrong guess here tells
  // someone the app is not for them before they have used it.
  say("Welcome to Lemma.");
  truth.textContent = "";
  if (entering) document.getElementById("choose-cloud").focus({ preventScroll: true });
}
function showLocalConfirmation() {
  scene.classList.remove("welcoming");
  scene.classList.add("choosing");
  chooseEl.hidden = true;
  localConfirmEl.hidden = false;
  switchModeBtn.hidden = true;
  localSetupError.hidden = true;
  hideOperationStatus();
  say("Set up Lemma on this Mac?");
  truth.textContent = "review before installation";
  // The button that was focused has just been hidden, which drops focus to
  // <body>: a keyboard user lands nowhere and a screen reader announces
  // nothing. `showChooser` already moves focus on the way in; this is the
  // same courtesy on the way forward.
  confirmLocalBtn.focus({ preventScroll: true });
}
function hideConnectionChoice() {
  scene.classList.remove("choosing", "welcoming");
  chooseEl.hidden = true;
  localConfirmEl.hidden = true;
}
document.getElementById("choose-local").addEventListener("click", showLocalConfirmation);
document.getElementById("back-to-choices").addEventListener("click", showChooser);
confirmLocalBtn.addEventListener("click", async () => {
  confirmLocalBtn.disabled = true;
  confirmLocalBtn.textContent = "Starting…";
  hideConnectionChoice();
  showOperationStatus("Preparing local Lemma.", "Starting the private runtime", "2%");
  truth.textContent = "first setup in progress · keep lemma open";
  try {
    await window.lemmaDesktop.setConnectionMode("local");
    switchModeBtn.hidden = false;
  } catch (error) {
    showLocalConfirmation();
    localSetupError.textContent = String(error || "Local setup could not start.");
    localSetupError.hidden = false;
    confirmLocalBtn.disabled = false;
    confirmLocalBtn.textContent = "Try local setup again";
  }
});
document.getElementById("choose-cloud").addEventListener("click", async () => {
  cloudSetupError.hidden = true;
  hideConnectionChoice();
  say("Taking you to Lemma Cloud.");
  truth.textContent = "lemma.work";
  try {
    await window.lemmaDesktop.setConnectionMode("hosted");
  } catch (error) {
    showChooser();
    // Said where the button is, as an alert, like the local setup's failure.
    // It was a lowercase footnote in the status line -- which the welcome
    // screen hides, so nobody saw why pressing the button did nothing.
    cloudSetupError.textContent = `Couldn't open Lemma Cloud. Check your connection and try again.${error ? ` (${String(error)})` : ""}`;
    cloudSetupError.hidden = false;
    appendLog(`ERROR ${String(error)}`);
  }
});

// ---- boot ----------------------------------------------------------------
shownLine = openIntent === "quit" ? "Stopping Lemma."
  : openIntent === "stop" ? VOICE.stopping[0] : VOICE.boot[0];
lineEl.textContent = shownLine;
truth.textContent = "";
window.lemmaDesktop.onLog(appendLog);
window.lemmaDesktop.onState(renderState);
refreshDiagnosticLog();
// The shell owns launch and connection-mode startup. Loading a document is
// only observation: an automatic start here races the shell's request.
// Explicit Start and Retry buttons still send their own user actions.
window.lemmaDesktop.getState().then((s) => {
  if (openIntent === "quit") {
    renderState(s);
  } else if (s && s.mode === "undecided") {
    showChooser();
  } else if (openIntent === "stop") {
    // Say what is actually happening, and never offer to start what the
    // user is in the middle of stopping.
    say(VOICE.stopping[0]);
    truth.textContent = VOICE.stopping[1];
    openBtn.hidden = true;
    progressEl.classList.add("active");
    bar.style.width = "100%";
  } else {
    renderState(s);
  }
});
