// The Diagnostics log viewer: which log, where it was read up to, and
// whether the view follows new lines.

import { $, invoke } from "./core.js";

/* ---------------------------------------------------------------- logs ---
 * The page that exists to explain a problem shows the logs, not just their
 * paths. The backing command tails by cursor, survives rotation and redacts
 * secrets.
 */

let activeLogSource = "locald";
let logCursor = null;
let logTimer = null;
let logFollow = true;

// A line is only coloured when it genuinely reports a failure. Anchored to
// word boundaries so a path like `.../error_handling/` does not light up.
const LOG_BAD = /\b(error|fatal|panic|failed|failure|traceback|refused|denied)\b/i;
const LOG_WARN = /\b(warn|warning|retry|retrying|timeout|timed out)\b/i;

function renderLogLines(text) {
  const view = $("diag-log");
  view.textContent = "";
  const body = (text ?? "").replace(/\s+$/, "");
  if (!body) {
    const empty = document.createElement("span");
    empty.className = "empty";
    empty.textContent = "No entries yet.";
    view.appendChild(empty);
    return;
  }
  for (const line of body.split("\n")) {
    const row = document.createElement("span");
    row.className = "line";
    if (LOG_BAD.test(line)) row.classList.add("bad");
    else if (LOG_WARN.test(line)) row.classList.add("warn");
    // textContent, never innerHTML: this is the one surface here rendering
    // text the app did not author.
    row.textContent = line;
    view.appendChild(row);
  }
  if (logFollow) view.scrollTop = view.scrollHeight;
}

function renderLogTabs(sources) {
  const tabs = $("diag-log-tabs");
  if (!Array.isArray(sources) || tabs.dataset.rendered === String(sources.length)) return;
  tabs.dataset.rendered = String(sources.length);
  tabs.textContent = "";
  for (const source of sources) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = source.label;
    button.classList.toggle("active", source.id === activeLogSource);
    button.addEventListener("click", () => selectLogSource(source.id));
    tabs.appendChild(button);
  }
}

let logBody = "";

async function refreshLog({ append = false } = {}) {
  try {
    const read = await invoke("diagnostic_logs", {
      source: activeLogSource,
      cursor: append ? logCursor : null,
    });
    if (append && read.entries && !read.entries.startsWith("No ")) {
      logBody += read.entries;
    } else if (!append) {
      logBody = read.entries || "";
    }
    logCursor = read.nextCursor;
    renderLogTabs(read.sources);
    renderLogLines(logBody);
  } catch (error) {
    renderLogLines(`Could not read the ${activeLogSource} log: ${String(error)}`);
  }
}

async function selectLogSource(source) {
  activeLogSource = source;
  logCursor = null;
  logBody = "";
  $("diag-log-tabs").dataset.rendered = "";
  await refreshLog();
}

export function startLogPolling() {
  stopLogPolling();
  refreshLog();
  logTimer = setInterval(() => refreshLog({ append: true }), 1000);
}

export function stopLogPolling() {
  clearInterval(logTimer);
  logTimer = null;
}

/** The follow toggle, and following that turns itself off on scroll. */
export function wireLogControls() {
  // Following pins the view to the newest line; scrolling up is how a person
  // reads what already happened, so that turns it off rather than fighting them.
  $("log-follow").addEventListener("click", () => {
    logFollow = !logFollow;
    $("log-follow").setAttribute("aria-pressed", String(logFollow));
    $("log-follow").textContent = logFollow ? "Following" : "Paused";
    if (logFollow) {
      const view = $("diag-log");
      view.scrollTop = view.scrollHeight;
    }
  });
  $("diag-log").addEventListener("scroll", (event) => {
    const view = event.currentTarget;
    const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 24;
    if (atBottom === logFollow) return;
    logFollow = atBottom;
    $("log-follow").setAttribute("aria-pressed", String(logFollow));
    $("log-follow").textContent = logFollow ? "Following" : "Paused";
  });
}
