import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../ui/control.js', import.meta.url), 'utf8');

function fixture() {
  const elements = new Map();
  const classes = () => {
    const values = new Set();
    return { add: (v) => values.add(v), remove: (v) => values.delete(v), contains: (v) => values.has(v) };
  };
  const pages = ['ai', 'integrations', 'channels'].map((page) => ({ dataset: { page }, classList: classes() }));
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: '', dataset: {}, classList: classes(), checked: false,
      closest: () => pages[0],
    });
    return elements.get(id);
  };
  const config = {
    revision: 1,
    ai: { protocol: 'openai_compat', base_url: 'https://saved.example/v1', models: ['saved'], default_model: 'saved', vision_models: [] },
    integrations: {}, surfaces: {},
  };
  const context = vm.createContext({
    snapshot: { operator: { config, secrets: { 'ai.api_key': true } } },
    filling: false, $: element, sectionRevisions: new Map(), pendingSaves: new Map(), draftVersions: new Map(),
    structuredClone, discoveredModels: [], csv: (value) => value.split(',').map((item) => item.trim()).filter(Boolean),
    renderDiscoveredModels() {}, clearDiscoveredModels() {}, secretInputs: () => [], paintConfigStates() {},
    document: {
      querySelectorAll: () => pages,
      querySelector: (selector) => pages.find((page) => selector.includes(`"${page.dataset.page}"`)),
    },
    toast() {}, friendlyError: String,
    providerDraftIdentity: () => element('ai-base').value,
  });
  return { context, element, pages };
}

function load(context, start, end) {
  vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end)), context);
}

test('a health snapshot preserves an unsaved provider draft', () => {
  const { context, element, pages } = fixture();
  pages[0].classList.add('dirty');
  element('ai-base').value = 'https://unsaved.example/v1';
  load(context, 'function fillConfiguration(', 'function collectConfiguration(');
  vm.runInContext('fillConfiguration()', context);
  assert.equal(element('ai-base').value, 'https://unsaved.example/v1');
  assert.equal(pages[0].classList.contains('dirty'), true);
});

test('model discovery leaves an unchanged saved key absent on the wire', async () => {
  const { context, element } = fixture();
  let payload;
  context.invoke = async (_command, args) => { payload = args.payload; return []; };
  context.applyDiscoveredModels = () => {};
  load(context, 'async function discoverModels(', 'function applyDiscoveredModels(');
  element('ai-base').value = 'https://saved.example/v1';
  element('ai-protocol').value = 'openai_compat';
  await vm.runInContext('discoverModels()', context);
  assert.equal(Object.hasOwn(payload, 'api_key'), false);
});

test('a discovery response cannot populate a different provider draft', async () => {
  const { context, element } = fixture();
  let resolve;
  let applied = false;
  context.invoke = () => new Promise((done) => { resolve = done; });
  context.applyDiscoveredModels = () => { applied = true; };
  load(context, 'async function discoverModels(', 'function applyDiscoveredModels(');
  element('ai-base').value = 'https://first.example/v1';
  const pending = vm.runInContext('discoverModels()', context);
  element('ai-base').value = 'https://second.example/v1';
  resolve(['old-provider-model']);
  await pending;
  assert.equal(applied, false);
});

test('saving integrations excludes provider and channel drafts and their secrets', () => {
  const { context, element, pages } = fixture();
  const secret = { value: 'replacement', dataset: { secret: 'integrations.deepgram_api_key' }, closest: () => pages[1] };
  context.secretInputs = () => [secret, { value: 'unsaved-api-key', dataset: { secret: 'ai.api_key' }, closest: () => pages[0] }];
  context.sectionRevisions.set('integrations', 7);
  element('google-id').value = 'draft-google';
  element('ai-base').value = 'https://unsaved.example';
  load(context, 'function collectConfiguration(', 'async function saveConfiguration(');
  const patch = vm.runInContext('collectConfiguration("integrations")', context);
  assert.equal(patch.expected_revision, 7);
  assert.equal(patch.section.name, 'integrations');
  assert.equal(patch.section.value.google_client_id, 'draft-google');
  assert.deepEqual(Object.keys(patch.secrets), ['integrations.deepgram_api_key']);
  assert.equal(patch.secrets['integrations.deepgram_api_key'].action, 'replace');
  assert.equal(Object.hasOwn(patch, 'config'), false);
});

test('secret keep and remove are distinct from replacement', () => {
  const { context, pages } = fixture();
  const secret = { value: '', dataset: { secret: 'ai.api_key', clear: 'false' }, closest: () => pages[0] };
  context.secretInputs = () => [secret];
  load(context, 'function collectConfiguration(', 'async function saveConfiguration(');
  assert.equal(vm.runInContext('collectConfiguration("ai")', context).secrets['ai.api_key'].action, 'keep');
  secret.dataset.clear = 'true';
  assert.equal(vm.runInContext('collectConfiguration("ai")', context).secrets['ai.api_key'].action, 'remove');
});

test('typing a replacement cancels a previously armed credential removal', () => {
  const { context, element } = fixture();
  const key = element('ai-key');
  key.dataset = { secret: 'ai.api_key', clear: 'true' };
  key.value = 'replacement';
  key.parentElement = { querySelector: () => null };
  context.key = key;
  load(context, 'function markDirty(', 'function secretInputs(');
  vm.runInContext('markDirty(key)', context);
  assert.equal(key.dataset.clear, 'false');
});

test('an existing snapshot does not prevent reconnect or hide an outage', () => {
  const { context, element } = fixture();
  let retry;
  let requested = false;
  context.snapshotRetryTimer = null;
  context.SNAPSHOT_RETRY_MS = 5000;
  context.setTimeout = (callback) => { retry = callback; return 1; };
  context.requestSnapshot = () => { requested = true; };
  load(context, 'function scheduleSnapshotRetry(', '/* Daemon errors,');
  load(context, 'function showSnapshotUnavailable(', 'function clearSnapshotUnavailable(');
  vm.runInContext('showSnapshotUnavailable("disconnected"); scheduleSnapshotRetry()', context);
  assert.equal(element('snapshot-unavailable').hidden, false);
  assert.equal(element('snapshot-unavailable-detail').textContent, 'disconnected');
  retry();
  assert.equal(requested, true);
});

test('closing a dirty page offers a decision before closing the webview', async () => {
  const { context, element, pages } = fixture();
  let closed = false;
  let prompted = false;
  context.document.querySelector = () => pages[0];
  context.invoke = async () => { closed = true; };
  element('unsaved-dialog').showModal = () => { prompted = true; };
  load(context, 'async function closeLocalSettings(', 'function markDirty(');
  assert.equal(await vm.runInContext('closeLocalSettings()', context), false);
  assert.equal(prompted, true);
  assert.equal(closed, false);
});

test('unsupported updates never invoke the installer or request a reset', async () => {
  const { context } = fixture();
  const commands = [];
  context.invoke = async (command) => { commands.push(command); };
  context.appUpdate = { dataCompatibility: 'requires-reset' };
  context.runtimeInfo = null;
  context.button = { dataset: { action: 'install-app-update' } };
  context.document.querySelectorAll = () => [];
  load(context, 'async function runDesktopAction(', 'function render()');
  await vm.runInContext('runDesktopAction(button)', context);
  assert.deepEqual(commands, []);
});

function eventFixture() {
  const fixtureState = fixture();
  const { context } = fixtureState;
  Object.assign(context, {
    state: null, sharingChoice: null, sharingBusy: false,
    clearSnapshotUnavailable() {}, fillConfiguration() {}, render() {}, requestSnapshot() {}, scheduleSnapshotRetry() {},
    setSectionError(page, message) { page.error = message; },
  });
  load(context, 'function handleLocaldEvent(', '\nconfigureInteractionHandlers();');
  return fixtureState;
}

test('save completion preserves edits made while the save was running', () => {
  const { context, pages } = eventFixture();
  pages[0].classList.add('dirty');
  context.draftVersions.set('ai', 2);
  let completed;
  context.pendingSaves.set('save', { page: pages[0], button: {}, original: 'Save', version: 1, complete: (value) => { completed = value; } });
  vm.runInContext('handleLocaldEvent({ event: "config.applied", id: "save", operator: {config: {revision: 2}} })', context);
  assert.equal(pages[0].classList.contains('dirty'), true);
  assert.equal(completed, true);
});

test('a conflict preserves the draft and leaves a persistent section error', () => {
  const { context, pages } = eventFixture();
  pages[0].classList.add('dirty');
  let completed;
  context.pendingSaves.set('save', { page: pages[0], button: {}, original: 'Save', complete: (value) => { completed = value; } });
  vm.runInContext('handleLocaldEvent({ event: "error", id: "save", code: "config-conflict", message: "Settings changed elsewhere" })', context);
  assert.equal(pages[0].classList.contains('dirty'), true);
  assert.equal(pages[0].error, 'Settings changed elsewhere');
  assert.equal(completed, false);
});

test('reconnecting recovers a save whose completion event was lost', () => {
  const { context, pages } = eventFixture();
  pages[0].classList.add('dirty');
  let completed;
  context.pendingSaves.set('save', { page: pages[0], button: {}, original: 'Save', version: 0, complete: (value) => { completed = value; } });
  vm.runInContext('handleLocaldEvent({ event: "control.snapshot", operator: {config: {revision: 2}}, config_operations: {save: {status: "succeeded", operator: {config: {revision: 2}}}} })', context);
  assert.equal(pages[0].classList.contains('dirty'), false);
  assert.equal(completed, true);
});

test('saving one section cannot silently rebase another draft past an unseen change', () => {
  const { context, pages } = eventFixture();
  context.sectionRevisions.set('ai', 1);
  context.sectionRevisions.set('integrations', 2);
  context.pendingSaves.set('save', { page: pages[1], button: {}, original: 'Save', version: 0, expectedRevision: 2, complete() {} });
  vm.runInContext('handleLocaldEvent({ event: "config.applied", id: "save", operator: {config: {revision: 3}} })', context);
  assert.equal(context.sectionRevisions.get('ai'), 1);
  assert.equal(context.sectionRevisions.get('integrations'), 3);
});
