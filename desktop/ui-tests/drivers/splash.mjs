// The splash under test, with the shell it talks to replaced by a fixture.
//
// `index.html` is loaded from disk through a route handler rather than a
// `file://` URL so that it runs on a real origin: `navigator.clipboard` and
// module scripts both behave differently on `file://`, and the page uses both.
//
// Extracted from onboarding.browser.mjs when a second suite needed it. The
// defaults reproduce that file's fixture exactly, so its tests are unchanged
// by the move; everything new is opt-in.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// What the app itself serves, so the harness cannot pass on an asset the app
// would refuse or refuse one the app serves. Tauri derives the type from the
// extension (`tauri-utils::mime_type`), and `.mjs` is `text/javascript` there.
const CONTENT_TYPES = {
  html: 'text/html',
  js: 'text/javascript',
  mjs: 'text/javascript',
  css: 'text/css',
  json: 'application/json',
  woff2: 'font/woff2',
};

/** The extension of `name`, refusing one nothing has a type for. */
function extension(name) {
  const found = name.includes('.') ? name.slice(name.lastIndexOf('.') + 1) : '';
  if (!Object.hasOwn(CONTENT_TYPES, found)) {
    throw new Error(
      `no content type for .${found}: add it to CONTENT_TYPES, and check the `
      + `app serves it too`,
    );
  }
  return found;
}

export const DEFAULT_STATE = {
  mode: 'undecided',
  phaseKey: 'boot',
  status: 'waiting',
  running: false,
  ready: false,
  error: false,
  setup: true,
};

export async function launchSplash(browser, t, {
  viewport = { width: 1100, height: 760 },
  windows = false,
  initialState = null,
  deferState = false,
  intent = '',
  colorScheme = 'light',
  logs = null,
  recoveryOptions = null,
  requests = null,
} = {}) {
  const context = await browser.newContext({
    viewport,
    reducedMotion: 'reduce',
    colorScheme,
    userAgent: windows ? 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
      : 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
  });
  t.after(() => context.close());
  const page = await context.newPage();
  // Every request the splash makes, from before the first navigation, so a
  // test can assert about what it did *not* fetch. Attaching this in the test
  // would be too late: the module scripts are requested by the first load.
  if (requests) page.on('request', request => requests.push(request.url()));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  const assets = new URL('../../ui/', import.meta.url);
  await page.route('https://desktop.test/**', async route => {
    const name = new URL(route.request().url()).pathname.slice(1);
    const file = new URL(name, assets);
    if (!file.href.startsWith(assets.href)) return route.abort();
    let contentType;
    try {
      contentType = CONTENT_TYPES[extension(name)];
    } catch (error) {
      // Louder than serving it as bytes. A module or a stylesheet delivered as
      // application/octet-stream is *refused* by the browser, and the page then
      // fails in whatever way the missing file happens to cause -- for a module
      // the whole importing script never runs, which arrives as every assertion
      // timing out and nothing saying why. This is how `screen-state.mjs` was
      // found, thirty seconds at a time.
      throw new Error(`${error.message} (requested by the page under test)`);
    }
    try {
      await route.fulfill({ body: await readFile(file), contentType });
    } catch {
      await route.abort();
    }
  });
  await page.addInitScript(({ initialState, defaultState, deferState, logs, recoveryOptions }) => {
    window.__fixture = { calls: [], rejectInstall: false, rejectOpen: false, rejectReset: false };
    window.__TAURI__ = {
      event: {
        listen: async (name, listener) => {
          if (name === 'lemma:state') window.__fixture.renderState = state => listener({ payload: state });
          // The live tail. Without it nothing can drive the log panel, which
          // is why it had no coverage at all.
          if (name === 'lemma:log') window.__fixture.emitLog = line => listener({ payload: line });
        },
      },
      core: {
        async invoke(command, args) {
          window.__fixture.calls.push({ command, args });
          if (command === 'get_state' && deferState) return new Promise(() => {});
          if (command === 'get_state') return initialState || defaultState;
          if (command === 'diagnostic_logs') {
            const source = args?.source || 'events';
            // A cursor means "what is new since then". Returning the same
            // body again would make the panel's one-second poll grow the
            // transcript by a copy per second.
            const fresh = !args?.cursor;
            return {
              source,
              entries: fresh ? (logs?.entries?.[source] ?? '') : '',
              nextCursor: `cursor:${source}`,
              sources: logs?.sources ?? [],
            };
          }
          if (command === 'local_recovery_options') return recoveryOptions || {};
          if (command === 'open_app' && window.__fixture.rejectOpen) {
            throw new Error('the workspace window did not open');
          }
          if (
            (command === 'reset_local_data' || command === 'reset_full_reinstall')
            && window.__fixture.rejectReset
          ) {
            throw new Error('the runtime is still holding local data');
          }
          if (command === 'set_connection_mode' && window.__fixture.rejectInstall) {
            throw new Error('Not enough disk space for the local runtime. Free space and retry.');
          }
          return undefined;
        },
      },
    };
  }, { initialState, defaultState: DEFAULT_STATE, deferState, logs, recoveryOptions });
  await page.goto(`https://desktop.test/index.html?intent=${encodeURIComponent(intent)}`);
  if (!initialState) await page.locator('#choose').waitFor({ state: 'visible' });
  return page;
}
