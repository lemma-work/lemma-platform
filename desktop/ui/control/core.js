// What every panel of Local settings shares: the bridge to the shell, the
// DOM helpers, the wording of errors, and the state more than one panel writes.

// Read at call time rather than import time, so a test can load these
// modules first and supply the bridge afterwards.
export const invoke = (command, args = {}) => window.__TAURI__.core.invoke(command, args);
export const listen = (event, handler) =>
  window.__TAURI__.event.listen(event, ({ payload }) => handler(payload));
export const $ = (id) => document.getElementById(id);

// This window ships in the Windows build too, and every sentence about the
// machine said "Mac" -- including the recovery panel that names what is about
// to be deleted, which is the worst place to describe hardware the reader does
// not own. `forThisDevice` covers the strings written from JS; the walk at the
// bottom of control.js covers the ones written in control.html.
export const IS_WINDOWS = /Windows/i.test(navigator.userAgent);
export const forThisDevice = (text) =>
  IS_WINDOWS ? String(text).replace(/\bthis Mac\b/g, "this PC") : String(text);
// Named per platform because the sentence is about the operating system
// refusing access, not about the box it runs on.
const OS_NAME = IS_WINDOWS ? "Windows" : "macOS";
export const LOCAL_MODE = window.__LEMMA_DESKTOP__?.mode === "local";
export const LOCAL_PAGES = new Set(["overview"]);

export function toast(message, error = false) {
  const element = $("toast");
  element.textContent = message;
  element.className = `toast${error ? " error" : ""}`;
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.hidden = true; }, 5500);
}

// Escapes quotes as well as angle brackets and ampersands.
//
// The `textContent` -> `innerHTML` trick handles `<`, `>` and `&` but leaves
// quotes alone, and several call sites interpolate into a double-quoted
// attribute value. That was inert while every value was an internal constant;
// it stops being inert now that this page renders log lines, which carry error
// strings, URLs and agent tool output. Log bodies themselves go through
// `textContent` rather than here -- this covers the attribute cases.
export function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML.replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

// A plain confirm() returns false without drawing anything in this webview, so
// destructive buttons ask the shell for a native dialog instead.
export const confirmAction = (title, message, confirmLabel) =>
  invoke("confirm_destructive_action", { title, message, confirmLabel });

export function summaryHtml(title, copy, status, page) {
  return `<div class="summary-row"${page ? ` data-summary-page="${escapeHtml(page)}"` : ""}><span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(copy)}</small></span><span class="status">${escapeHtml(status)}</span></div>`;
}

export function serviceHtml(title, copy, status, tone) {
  return `<div class="service-row"><span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(copy)}</small></span><span class="status ${tone}">${escapeHtml(status)}</span></div>`;
}

// What each dot's colour means, in words.
//
// The dots were colour and nothing else: green, gold, red and grey, with no
// text anywhere. Someone who cannot tell those apart -- or who is listening
// rather than looking -- got eleven navigation items that all read the same,
// and no way to find the one that needs them.
const DOT_MEANING = {
  ok: "healthy",
  warn: "needs attention",
  bad: "not working",
  "": "no state to report",
};

export function setDot(id, tone) {
  const dot = $(`dot-${id}`);
  if (!dot) return;
  dot.className = `health-dot ${tone}`;
  // Inside the nav button, so it joins that button's name: "AI provider,
  // needs attention".
  dot.setAttribute("role", "img");
  dot.setAttribute("aria-label", DOT_MEANING[tone] ?? DOT_MEANING[""]);
}

export async function copyText(value) {
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

/* Daemon errors, said in a way a person can act on.
 *
 * These strings are `io::Error` and `Err(String)` values from locald, written
 * for whoever is reading a stack trace. Shown verbatim they tell a user
 * "control endpoint unavailable: No such file or directory (os error 2)",
 * which names an internal component, describes a syscall, and suggests
 * nothing. The raw text is still available in the logs below.
 */
const FRIENDLY_ERRORS = [
  [/control endpoint unavailable|is not connected|disconnected/i,
   "Lemma's background service isn't running. Starting Lemma usually brings it back."],
  [/control token/i,
   "Lemma couldn't authenticate with its own background service. Restarting Lemma replaces the credential."],
  [/local data must be reset/i,
   "This version cannot read the existing workspace data. Keep the data and return to a compatible version, or use Recovery only if you intend to erase it."],
  [/another local operation is running|busy/i,
   "Lemma is already doing something. Wait for it to finish and try again."],
  [/broken pipe|connection reset/i,
   "The background service stopped mid-request. Try again."],
  [/permission denied/i,
   `${OS_NAME} refused Lemma access to its own files. ` +
   (IS_WINDOWS
     ? "Check that Lemma is installed for this user and try again."
     : "Check that Lemma is in Applications and try again.")],
];

export function friendlyError(reason) {
  const text = String(reason ?? "");
  const match = FRIENDLY_ERRORS.find(([pattern]) => pattern.test(text));
  return forThisDevice(match ? match[1] : text.replace(/^Error:\s*/, ""));
}

/**
 * The state more than one panel reads and writes.
 *
 * One object rather than module-level `let`s, because an ES module cannot
 * assign another module's binding: a panel that set `snapshot` directly would
 * set a copy nobody else sees.
 */
export const store = {
  /** The daemon's last full picture, and its `state` block. */
  snapshot: null,
  state: null,
  runtimeInfo: null,
  appUpdate: null,
  sharingBusy: false,
};

let requestCounter = 0;
export const nextId = (prefix) => `control-${prefix}-${Date.now()}-${++requestCounter}`;
