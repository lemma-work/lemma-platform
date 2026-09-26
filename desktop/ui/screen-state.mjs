//! What a daemon snapshot means, decided in one place.
//
// `render()` used to be the state machine: a chain of `if`s that each mutated
// half a dozen elements and returned early, with the screen's identity implied
// by which branch had run and by CSS classes left on `.scene`. Nothing could
// ask it what it thought the state was, so nothing tested it -- on the function
// that decides what every screen shows, and that schedules the move into the
// workspace.
//
// These two functions are the decisions. The copy and the DOM stay in
// index.html; what moved is the part that has an answer worth checking.

/** The diagnostic logs the shell serves. `read_diagnostic_log` refuses any
 *  other id, so a source outside this set is an error panel, not a log. */
export const LOG_SOURCES = [
  "events",
  "migrations",
  "backend",
  "frontend",
  "vm",
  "guest",
  "locald",
  "locald-stderr",
  "agent-host",
  "installer",
  "launch",
];

/** Which log is worth opening while a given phase is running. */
const PHASE_LOG = {
  download: "installer",
  check: "installer",
  infra: "vm",
  workspace: "vm",
  migrations: "migrations",
  backend: "backend",
  frontend: "frontend",
  verify: "backend",
};

/** Which log explains a given failure. */
const ERROR_LOG = {
  "wsl-required": "installer",
  "wsl-setup-denied": "installer",
  "wsl-reboot-required": "installer",
  "runtime-install-failed": "installer",
  "runtime-prepare-failed": "vm",
  "runtime-recovery": "vm",
  "guest-kernel-failed": "vm",
  "managed-runtime-unavailable": "vm",
  "managed-runtime-recovery-failed": "vm",
  "local-data-incompatible": "vm",
  "local-data-reset-incomplete": "vm",
  "locald-start-failed": "locald-stderr",
  "locald-disconnected": "locald",
};

/** Failures that a retry cannot change, because nothing has changed. */
const UNRETRYABLE = ["local-data-incompatible"];

/**
 * The only failures that offer to erase local data.
 *
 * This list used to include every way the stack can fail to come up -- the
 * local service stopping, the runtime not installing -- and each of those put
 * "Reset local data" beside Try again. Neither failure has anything to do with
 * the data: a service that crashed or a download that stalled is fixed by
 * starting again, and somebody who pressed the button the screen offered lost
 * everything for a network blip. A reset is offered where the data itself is
 * the problem, and nowhere else; Recovery still has it for anyone who goes
 * looking.
 */
const DATA_IS_THE_PROBLEM = ["local-data-incompatible", "local-data-reset-incomplete"];

/** The local service manager went away, or never came up. */
const SERVICE_STOPPED = ["locald-start-failed", "locald-disconnected"];

/** Where the releases live, for somebody who needs a different version. The
 *  updater reads the same repository (`release_info.rs`). */
export const RELEASES_URL = "https://github.com/lemma-work/lemma-platform/releases";

/**
 * Whether unreadable data was written by a different release, rather than
 * locked by credentials that were replaced.
 *
 * Both arrive as `local-data-incompatible` -- the daemon maps one marker
 * phrase to one code -- but only the first is fixed by running another
 * version, and offering "get the previous version" for the second sends
 * somebody to download an app that cannot open their data either. guestd's
 * two version messages are the only ones that name PostgreSQL, so that word
 * is what tells them apart.
 */
function writtenByAnotherRelease(state) {
  return state?.errorCode === "local-data-incompatible" && /postgresql/i.test(String(state?.status || ""));
}

/** A download failure, as `actionable_runtime_install_error` words one. */
function downloadFailed(state) {
  return /download|github\.com/i.test(String(state?.status || ""));
}

/**
 * The headline, the one-line note, and the extra actions for a failure.
 *
 * "Something stopped." was the headline for every failure there is, so the
 * reader learned nothing from it that the error box did not say better. Each
 * failure with a known cause now says which thing stopped.
 */
function describeFailure(state, code) {
  if (NEEDS_WINDOWS_SETUP.includes(code)) return { headline: "Windows needs one permission.", note: "" };
  if (code === "wsl-reboot-required") return { headline: "One restart, then Lemma continues.", note: "" };
  if (code === "runtime-install-failed") {
    return {
      headline: "Lemma couldn't finish installing.",
      // Only a download failure is fixed by being online; saying so for a
      // bad disk or a mismatched build would send somebody to check a
      // connection that was never the problem.
      note: downloadFailed(state)
        ? "Your data is untouched. Lemma needs the internet once to finish installing."
        : "Your data is untouched.",
    };
  }
  if (SERVICE_STOPPED.includes(code)) {
    return {
      headline: "Lemma's local service stopped.",
      note: code === "locald-disconnected" ? "Your data is untouched." : "",
    };
  }
  if (writtenByAnotherRelease(state)) {
    return {
      headline: "This version of Lemma can't open your existing data.",
      note: "Your data is still on this computer.",
    };
  }
  if (code === "local-data-incompatible") return { headline: "Lemma can't open your existing data.", note: "" };
  if (code === "local-data-reset-incomplete") return { headline: "Resetting local data didn't finish.", note: "" };
  return { headline: "Something stopped.", note: "" };
}

const NEEDS_WINDOWS_SETUP = ["wsl-required", "wsl-setup-denied"];

/**
 * The diagnostic log to open for this state.
 *
 * The daemon names one in its phase and error events, and that is the answer
 * whenever it is a log the shell serves -- it saw the raw failure, before
 * anything reshaped it for a person to read.
 *
 * The fallback used to be a second guess made from the same words after they
 * had been reshaped: `phaseKey`, `status` and `errorCode` joined together and
 * searched for "migration", "frontend", "download", "container". Both keys are
 * enumerated values, so there is nothing to guess -- and matching prose meant
 * an error whose *message* happened to say "install" chose the installer log
 * over the one that recorded the failure.
 *
 * Anything unrecognised is the events log, which always exists.
 */
export function diagnosticSourceForState(state, served = LOG_SOURCES) {
  const named = state?.logSource;
  if (named && served.includes(named)) return named;
  const byError = ERROR_LOG[state?.errorCode];
  if (byError && served.includes(byError)) return byError;
  const byPhase = PHASE_LOG[state?.phaseKey];
  if (byPhase && served.includes(byPhase)) return byPhase;
  return "events";
}

/**
 * Which screen a snapshot means, and what the screen offers.
 *
 * `context` carries the three things that are not in the snapshot: the phase
 * last seen (a state with no phase is still a state), whether this run has ever
 * been a first setup, and whether the user has asked to stop.
 */
export function deriveScreen(state, context = {}) {
  const { lastPhase = "boot", sawSetup = false, isShuttingDown = false } = context;
  const phaseKey = state?.phaseKey || lastPhase || "boot";
  const setup = sawSetup || Boolean(state?.setup);
  const base = {
    phaseKey,
    sawSetup: setup,
    logSource: diagnosticSourceForState(state),
    errorDetail: "",
    showOpen: false,
    showRetry: false,
    showPrepareWindows: false,
    showResetData: false,
    showFullReinstall: false,
    showRecovery: false,
    keepDataUrl: "",
    resetLabel: "Reset local data",
    headline: "",
    note: "",
    primaryAction: null,
    progressActive: false,
    progressWidth: Math.min(100, state?.progress || 0),
    runInfo: setup ? "first run · only once" : "quick start",
  };

  // Before anything else: a user who has not chosen a mode is shown the
  // choice, not an empty screen behind a phase they never asked for.
  if (state?.mode === "undecided") {
    return { ...base, screen: "choosing", orb: "resume", whispers: "idle" };
  }

  if (state?.error) {
    const code = state.errorCode;
    const windowsSetup = NEEDS_WINDOWS_SETUP.includes(code);
    const windowsRestart = code === "wsl-reboot-required";
    const unreadable = UNRETRYABLE.includes(code);
    const otherRelease = writtenByAnotherRelease(state);
    const { headline, note } = describeFailure(state, code);
    return {
      ...base,
      screen: "error",
      windowsSetup,
      windowsRestart,
      headline,
      note,
      errorDetail: state.status || "startup failed",
      // Retrying an unreadable data directory reproduces it exactly, and
      // offering Try again is how somebody learns the app has nothing for them.
      showRetry: !windowsSetup && !windowsRestart && !unreadable,
      showPrepareWindows: windowsSetup,
      showResetData: DATA_IS_THE_PROBLEM.includes(code),
      showFullReinstall: unreadable,
      // Recovery beside the error for the failures that used to carry a
      // reset: it is where the reset went, behind a page that explains it.
      showRecovery: SERVICE_STOPPED.includes(code) || code === "runtime-install-failed",
      // Data another release wrote is kept by going back to that release,
      // which is the first thing to offer: erasing it is the second.
      keepDataUrl: otherRelease ? RELEASES_URL : "",
      resetLabel: otherRelease ? "Erase and start fresh" : "Reset local data",
      orb: "stall",
      whispers: "idle",
    };
  }

  // A snapshot taken before the stop was admitted still says ready. Acting on
  // it would offer to open a workspace that is being shut down -- and the
  // splash takes that offer on the user's behalf after a moment.
  if (state?.ready && isShuttingDown) {
    return {
      ...base,
      screen: "stopping",
      orb: "resume",
      whispers: "stop",
      progressActive: true,
      progressWidth: 100,
    };
  }

  if (state?.ready) {
    return {
      ...base,
      screen: "ready",
      showOpen: true,
      primaryAction: "open",
      orb: "awake",
      whispers: "stop",
      runInfo: "",
    };
  }

  if (phaseKey === "stopped") {
    return {
      ...base,
      screen: "stopped",
      showOpen: true,
      primaryAction: "start",
      orb: "resume",
      whispers: "idle",
    };
  }

  return {
    ...base,
    screen: "working",
    showRetry: true,
    orb: "resume",
    whispers: "run",
    progressActive: true,
  };
}
