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

/** Failures that mean the stack could not be brought up at all. */
const CANNOT_START = [
  "locald-start-failed",
  "locald-disconnected",
  "runtime-install-failed",
];

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
    return {
      ...base,
      screen: "error",
      windowsSetup,
      windowsRestart,
      errorDetail: state.status || "startup failed",
      // Retrying an unreadable data directory reproduces it exactly, and
      // offering Try again is how somebody learns the app has nothing for them.
      showRetry: !windowsSetup && !windowsRestart && !unreadable,
      showPrepareWindows: windowsSetup,
      showResetData: unreadable || CANNOT_START.includes(code),
      showFullReinstall: unreadable,
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
