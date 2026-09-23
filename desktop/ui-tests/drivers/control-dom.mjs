// The Local settings modules, loaded in Node against just enough DOM.
//
// These tests used to slice function bodies out of one 1,700-line control.js
// and run each in a hand-built context that re-declared whatever it happened to
// read. So a test exercised a copy of a function next to stand-ins for its
// neighbours, and passed as long as the text between two markers still parsed.
// Now the page is modules, and a test imports the real ones: a function calls
// the real functions it calls, and only the shell and the DOM are faked.

const ui = new URL('../../ui/control/', import.meta.url);

function classes() {
  const values = new Set();
  return {
    add: (value) => values.add(value),
    remove: (value) => values.delete(value),
    contains: (value) => values.has(value),
    toggle(value, force) {
      const on = force ?? !values.has(value);
      if (on) values.add(value);
      else values.delete(value);
      return on;
    },
  };
}

/** An element that accepts anything the settings code does to one. */
export function fakeElement(overrides = {}) {
  const children = [];
  const attributes = new Map();
  const listeners = {};
  return {
    value: '',
    textContent: '',
    innerHTML: '',
    className: '',
    hidden: false,
    disabled: false,
    checked: false,
    dataset: {},
    style: {},
    classList: classes(),
    children,
    listeners,
    setAttribute: (name, value) => attributes.set(name, String(value)),
    getAttribute: (name) => attributes.get(name) ?? null,
    removeAttribute: (name) => attributes.delete(name),
    addEventListener: (type, handler) => { (listeners[type] ??= []).push(handler); },
    appendChild: (child) => { children.push(child); return child; },
    append: (...added) => { children.push(...added); },
    replaceChildren: (...added) => { children.splice(0, children.length, ...added); },
    querySelector: (selector) => (selector === '.section-error'
      ? children.find((child) => String(child.className).includes('section-error')) ?? null
      : null),
    querySelectorAll: () => [],
    closest: () => null,
    parentElement: null,
    focus() {},
    blur() {},
    scrollTo() {},
    remove() {},
    select() {},
    ...overrides,
  };
}

/**
 * Install the globals the modules read, and return handles to them.
 *
 * `queries` and `lists` answer `document.querySelector(All)` by exact
 * selector; anything not listed finds nothing, which is what an absent
 * element does in the real page too.
 */
export function installDom() {
  const elements = new Map();
  const queries = new Map();
  const lists = new Map();
  const commands = [];
  const toasts = [];
  let invoke = async () => undefined;
  const document = {
    body: fakeElement(),
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, fakeElement({ id }));
      return elements.get(id);
    },
    querySelector: (selector) => queries.get(selector) ?? null,
    querySelectorAll: (selector) => lists.get(selector) ?? [],
    createElement: (tag) => fakeElement({ tagName: tag.toUpperCase() }),
    addEventListener() {},
    execCommand() {},
  };
  // Defined rather than assigned: Node has a read-only `navigator` of its own.
  const define = (name, value) =>
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  define('document', document);
  define('navigator', { userAgent: 'Macintosh', clipboard: { writeText: async () => {} } });
  define('window', {
    __LEMMA_DESKTOP__: { mode: 'local' },
    __TAURI__: {
      core: {
        invoke: (command, args) => {
          commands.push({ command, args });
          return invoke(command, args);
        },
      },
      event: { listen: async () => () => {} },
    },
    addEventListener() {},
  });
  const dom = {
    element: (id) => document.getElementById(id),
    queries,
    lists,
    commands,
    toasts,
    /** What the shell answers, for the next calls. */
    answer(handler) { invoke = handler; },
  };
  return dom;
}

/** A settings page section, as `.config-page` elements are. */
export function fakePage(name) {
  return fakeElement({ dataset: { page: name } });
}

/**
 * A module, imported fresh.
 *
 * Only for a module whose private state a test must start clean -- a flag
 * that remembers it already loaded, a backoff that remembers how far it got.
 * Its imports are shared with everything else, so `core`'s store is the one
 * the rest of the page sees.
 */
let generation = 0;
export async function fresh(name) {
  generation += 1;
  return import(new URL(`${name}.js?fresh=${generation}`, ui).href);
}

export async function shared(name) {
  return import(new URL(`${name}.js`, ui).href);
}

/** Put the shared state back to how a newly opened page has it. */
export async function resetShared() {
  const core = await shared('core');
  Object.assign(core.store, {
    snapshot: null,
    state: null,
    runtimeInfo: null,
    appUpdate: null,
    filling: false,
    sharingChoice: null,
    sharingProvider: 'ngrok',
    cloudflareSetupChoice: null,
    sharingBusy: false,
  });
  core.sectionRevisions.clear();
  core.draftVersions.clear();
  core.pendingSaves.clear();
  return core;
}

/** The operator configuration a daemon sends, complete enough to render. */
export function operatorConfig(overrides = {}) {
  return {
    revision: 1,
    ai: {
      protocol: 'openai_compat',
      base_url: 'https://saved.example/v1',
      models: ['saved'],
      default_model: 'saved',
      vision_models: [],
    },
    integrations: {
      composio_enabled: false,
      google_client_id: '',
      microsoft_client_id: '',
    },
    surfaces: {
      slack_socket_mode: false,
      telegram_polling: false,
      teams_app_id: '',
      teams_tenant_id: '',
      whatsapp_phone_number_id: '',
      whatsapp_waba_id: '',
      resend_inbound_domain: '',
    },
    ...overrides,
  };
}
