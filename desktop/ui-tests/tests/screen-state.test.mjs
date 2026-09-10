import assert from 'node:assert/strict';
import test from 'node:test';

import {
  LOG_SOURCES,
  deriveScreen,
  diagnosticSourceForState,
} from '../../ui/screen-state.mjs';

/**
 * The daemon saw the raw failure, before anything reshaped it for a person to
 * read, so what it names is the answer -- as long as the shell serves it.
 */
test('a log source the daemon names is used, and one it cannot serve is not', () => {
  assert.equal(diagnosticSourceForState({ logSource: 'migrations' }), 'migrations');

  // `read_diagnostic_log` refuses an id it has no file for, so passing one
  // through gives the user an error panel where the log should be. The daemon
  // answered exactly this for a crashed guest kernel.
  assert.equal(diagnosticSourceForState({ logSource: 'infrastructure' }), 'events');
  assert.equal(diagnosticSourceForState({ logSource: '' }), 'events');
  assert.equal(diagnosticSourceForState(null), 'events');
});

/**
 * The fallback reads enumerated keys, not prose. Both of these used to be
 * decided by searching `phaseKey + status + errorCode` for words -- after the
 * message had already been rewritten for display.
 */
test('the fallback reads the error code and the phase, not the words around them', () => {
  assert.equal(
    diagnosticSourceForState({ errorCode: 'guest-kernel-failed' }),
    'vm',
  );
  assert.equal(
    diagnosticSourceForState({ errorCode: 'locald-start-failed' }),
    'locald-stderr',
  );
  assert.equal(diagnosticSourceForState({ phaseKey: 'frontend' }), 'frontend');
  assert.equal(diagnosticSourceForState({ phaseKey: 'download' }), 'installer');

  // A status that says "install" while the runtime is what failed used to win
  // over the error code, because "install" was tested before anything else.
  assert.equal(
    diagnosticSourceForState({
      errorCode: 'guest-kernel-failed',
      status: 'could not install the guest kernel module',
      phaseKey: 'infra',
    }),
    'vm',
  );

  // And a phase with no log of its own is the events log, not a guess.
  assert.equal(diagnosticSourceForState({ phaseKey: 'boot' }), 'events');
  assert.equal(diagnosticSourceForState({ errorCode: 'nothing-known' }), 'events');
});

test('every log this can choose is one the shell serves', () => {
  const states = [
    {}, { logSource: 'guest' }, { phaseKey: 'migrations' },
    { errorCode: 'wsl-required' }, { errorCode: 'runtime-recovery' },
    { errorCode: 'local-data-incompatible' }, { phaseKey: 'verify' },
    { phaseKey: 'workspace' }, { errorCode: 'locald-disconnected' },
  ];
  for (const state of states) {
    assert.ok(
      LOG_SOURCES.includes(diagnosticSourceForState(state)),
      `${JSON.stringify(state)} chose a log the shell does not serve`,
    );
  }
});

test('a state with no phase keeps the last one rather than rendering nothing', () => {
  assert.equal(deriveScreen({}, { lastPhase: 'backend' }).phaseKey, 'backend');
  assert.equal(deriveScreen({}, {}).phaseKey, 'boot');
});

test('an undecided mode is the chooser, whatever else the snapshot says', () => {
  const screen = deriveScreen({ mode: 'undecided', phaseKey: 'backend', progress: 40 }, {});
  assert.equal(screen.screen, 'choosing');
});

/**
 * Data this release cannot read: retrying reproduces it exactly, and offering
 * Try again is how somebody learns the app has nothing for them.
 */
test('an unreadable data directory offers recovery instead of a retry', () => {
  const screen = deriveScreen({ error: true, errorCode: 'local-data-incompatible', status: 'x' }, {});
  assert.equal(screen.screen, 'error');
  assert.equal(screen.showRetry, false);
  assert.equal(screen.showResetData, true);
  assert.equal(screen.showFullReinstall, true);
});

test('a Windows permission is a setup button, not a retry', () => {
  for (const errorCode of ['wsl-required', 'wsl-setup-denied']) {
    const screen = deriveScreen({ error: true, errorCode }, {});
    assert.equal(screen.showPrepareWindows, true, errorCode);
    assert.equal(screen.showRetry, false, errorCode);
    assert.equal(screen.windowsSetup, true, errorCode);
  }
  const restart = deriveScreen({ error: true, errorCode: 'wsl-reboot-required' }, {});
  assert.equal(restart.windowsRestart, true);
  assert.equal(restart.showRetry, false);
  assert.equal(restart.showPrepareWindows, false);
});

test('a stack that could not start offers a data reset but not a reinstall', () => {
  for (const errorCode of ['locald-start-failed', 'locald-disconnected', 'runtime-install-failed']) {
    const screen = deriveScreen({ error: true, errorCode }, {});
    assert.equal(screen.showResetData, true, errorCode);
    assert.equal(screen.showFullReinstall, false, errorCode);
    assert.equal(screen.showRetry, true, errorCode);
  }
});

test('an ordinary failure keeps Try again and nothing else', () => {
  const screen = deriveScreen({ error: true, errorCode: 'host-operation-failed', status: 'boom' }, {});
  assert.equal(screen.showRetry, true);
  assert.equal(screen.showResetData, false);
  assert.equal(screen.showFullReinstall, false);
  assert.equal(screen.errorDetail, 'boom');
});

test('an error with no status still says something', () => {
  assert.equal(deriveScreen({ error: true }, {}).errorDetail, 'startup failed');
});

/**
 * A snapshot taken before the stop was admitted still says ready. Acting on it
 * offers to open a workspace that is being shut down -- and the splash takes
 * that offer on the user's behalf after a moment.
 */
test('ready during a shutdown is the stopping screen, and offers nothing to open', () => {
  const screen = deriveScreen({ ready: true }, { isShuttingDown: true });
  assert.equal(screen.screen, 'stopping');
  assert.equal(screen.showOpen, false);
  assert.equal(screen.primaryAction, null);
  assert.equal(screen.progressWidth, 100);
});

test('ready opens, stopped starts, and each says which', () => {
  const ready = deriveScreen({ ready: true, progress: 100 }, {});
  assert.equal(ready.screen, 'ready');
  assert.equal(ready.primaryAction, 'open');
  assert.equal(ready.showOpen, true);
  assert.equal(ready.progressActive, false);
  assert.equal(ready.runInfo, '');

  const stopped = deriveScreen({ phaseKey: 'stopped' }, {});
  assert.equal(stopped.screen, 'stopped');
  assert.equal(stopped.primaryAction, 'start');
  assert.equal(stopped.showOpen, true);
  assert.equal(stopped.progressActive, false);
});

test('a run that has ever been a first setup keeps saying so', () => {
  assert.equal(deriveScreen({ setup: true, phaseKey: 'infra' }, {}).runInfo, 'first run · only once');
  // Carried, because a later snapshot does not repeat it.
  assert.equal(
    deriveScreen({ phaseKey: 'backend' }, { sawSetup: true }).runInfo,
    'first run · only once',
  );
  assert.equal(deriveScreen({ phaseKey: 'backend' }, {}).runInfo, 'quick start');
  assert.equal(deriveScreen({ setup: true }, {}).sawSetup, true);
});

test('progress is clamped, and only the working screen shows a bar', () => {
  assert.equal(deriveScreen({ phaseKey: 'backend', progress: 140 }, {}).progressWidth, 100);
  assert.equal(deriveScreen({ phaseKey: 'backend' }, {}).progressActive, true);
  assert.equal(deriveScreen({ error: true }, {}).progressActive, false);
  assert.equal(deriveScreen({ phaseKey: 'stopped' }, {}).progressActive, false);
});

/** Nothing may fall through: a state nobody handles leaves the static logo up. */
test('every snapshot resolves to a screen', () => {
  const snapshots = [
    {}, null, undefined, { phaseKey: 'boot' }, { running: true }, { setup: true },
    { error: true }, { ready: true }, { phaseKey: 'stopped' }, { mode: 'undecided' },
    { mode: 'local', phaseKey: 'verify', progress: 91 },
  ];
  const known = ['choosing', 'error', 'stopping', 'ready', 'stopped', 'working'];
  for (const snapshot of snapshots) {
    const screen = deriveScreen(snapshot, {});
    assert.ok(known.includes(screen.screen), `${JSON.stringify(snapshot)} -> ${screen.screen}`);
    assert.ok(LOG_SOURCES.includes(screen.logSource));
  }
});
