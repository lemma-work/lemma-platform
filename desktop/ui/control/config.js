// The operator configuration forms -- AI provider, integrations and
// channels -- and saving them.

import {
  $,
  csv,
  draftVersions,
  friendlyError,
  invoke,
  nextId,
  pendingSaves,
  sectionRevisions,
  store,
  toast,
} from "./core.js";
import { requestSnapshot } from "./events.js";

export function markDirty(target) {
  if (store.filling) return;
  if (target.dataset.secret && target.value) {
    target.dataset.clear = "false";
    const button = target.parentElement.querySelector(".secret-clear");
    if (button) {
      button.classList.remove("armed");
      button.textContent = "Remove";
      labelSecretButton(button, target);
    }
  }
  const page = target.closest(".config-page");
  if (page) {
    page.classList.add("dirty");
    draftVersions.set(page.dataset.page, (draftVersions.get(page.dataset.page) || 0) + 1);
  }
}

function secretInputs() {
  return [...document.querySelectorAll("input[data-secret]")];
}

export function labelSecretButton(button, input) {
  if (!button || !input) return;
  const label = input.labels?.[0]?.textContent?.trim() || "credential";
  button.setAttribute("aria-label", `${button.textContent} ${label}`);
}

// Loopback endpoints for the two local runners, and base URLs for the API
// providers. Local first: someone running Lemma on their own Mac most likely
// already has one of these serving, and it needs no key and no account.
const PROVIDER_PRESETS = {
  ollama: { protocol: "openai_compat", base: "http://127.0.0.1:11434/v1", note: "Ollama selected. Make sure it is running, then list its models." },
  lmstudio: { protocol: "openai_compat", base: "http://127.0.0.1:1234/v1", note: "LM Studio selected. Start its local server, then list its models." },
  openai: { protocol: "openai_compat", base: "https://api.openai.com/v1", note: "Enter an OpenAI API key, then list models." },
  anthropic: { protocol: "anthropic_compat", base: "https://api.anthropic.com", note: "Enter an Anthropic API key, then list models." },
  openrouter: { protocol: "openai_compat", base: "https://openrouter.ai/api/v1", note: "Enter an OpenRouter API key, then list models." },
};

// Models the provider reported for the endpoint currently in the form. Held
// here rather than in a text field because the point is that the user picks
// from what exists instead of typing an id they have to already know.
let discoveredModels = [];

export function applyProviderPreset(name) {
  const preset = PROVIDER_PRESETS[name];
  if (!preset) return;
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.classList.toggle("active", button.dataset.preset === name);
  });
  $("ai-protocol").value = preset.protocol;
  $("ai-base").value = preset.base;
  $("ai-key").value = "";
  $("ai-key").dataset.clear = "false";
  const clearButton = $("ai-key").parentElement.querySelector(".secret-clear");
  if (clearButton) {
    clearButton.classList.remove("armed");
    clearButton.textContent = "Remove";
    labelSecretButton(clearButton, $("ai-key"));
  }
  $("ai-private-network").checked = false;
  clearDiscoveredModels();
  markDirty($("ai-base"));
  toast(preset.note);
}

export function clearDiscoveredModels() {
  discoveredModels = [];
  $("ai-model-panel").hidden = true;
  $("ai-model").innerHTML = "";
  $("ai-model-count").textContent = "";
}

function renderDiscoveredModels(models, selected) {
  discoveredModels = models;
  const select = $("ai-model");
  select.innerHTML = "";
  models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    select.append(option);
  });
  select.value = models.includes(selected) ? selected : models[0] || "";
  $("ai-model-count").textContent = `${models.length} model${models.length === 1 ? "" : "s"} available`;
  $("ai-model-panel").hidden = models.length === 0;
}

function providerDraftIdentity() {
  return JSON.stringify([$("ai-protocol").value, $("ai-base").value.trim(),
    $("ai-private-network").checked, $("ai-key").value, $("ai-key").dataset.clear,
    store.snapshot?.operator.config.revision, draftVersions.get("ai")]);
}

export async function discoverModels() {
  const button = $("ai-discover");
  if (button.disabled) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Connecting…";
  const draft = providerDraftIdentity();
  try {
    const models = await invoke("discover_provider_models", {
      payload: {
        ai: {
          protocol: $("ai-protocol").value,
          base_url: $("ai-base").value.trim(),
          default_model: "",
          models: [],
          vision_models: [],
          allow_private_network: $("ai-private-network").checked,
        },
        ...($("ai-key").dataset.clear === "true"
          ? { api_key: "" }
          : $("ai-key").value ? { api_key: $("ai-key").value } : {}),
      },
    });
    // The command returns the list rather than announcing it on the event
    // stream, so the answer belongs to this press and not to whichever page
    // happened to be listening.
    if (draft === providerDraftIdentity()) applyDiscoveredModels(Array.isArray(models) ? models : []);
  } catch (error) {
    if (draft === providerDraftIdentity()) toast(friendlyError(error), true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function applyDiscoveredModels(models) {
  if (!models.length) {
    toast("That provider is reachable but reported no models.", true);
    return;
  }
  renderDiscoveredModels(models, $("ai-model").value);
  markDirty($("ai-model"));
  toast(`Found ${models.length} model${models.length === 1 ? "" : "s"}. Pick a default, then apply.`);
}

/* Whether a drawer holds anything the user has actually set.
 *
 * Read off the drawer's own fields rather than a per-provider table, so a row
 * added to the HTML is described correctly without anyone remembering to update
 * a list here. Secrets are never sent back to the page -- only their presence --
 * which is why they are checked against the snapshot instead of `value`.
 *
 * Deliberately "anything", not "everything": these rows hold different numbers
 * of credentials, several hold two independent ones, and no honest rule here
 * could say which are required for a given provider. The drawer still shows
 * exactly which fields are filled; this answers the question the collapsed row
 * has to answer, which is whether the user has been here at all.
 */
function drawerIsConfigured(drawer, presence) {
  const secrets = Array.from(drawer.querySelectorAll("input[data-secret]"));
  if (secrets.some((input) => presence[input.dataset.secret])) return true;
  const typed = Array.from(
    drawer.querySelectorAll('input:not([data-secret]):not([type="checkbox"])'),
  );
  if (typed.some((input) => input.value.trim())) return true;
  return Array.from(drawer.querySelectorAll('input[type="checkbox"]')).some(
    (input) => input.checked,
  );
}

/* The one fact a collapsed row has to carry.
 *
 * Before this the badge read "Optional" whether or not a key was saved, and the
 * only signal that one had been was the placeholder inside the input -- grey,
 * and invisible until the drawer was opened. Somebody who had just saved a
 * Deepgram key had no way to see that it landed.
 */
function paintConfigStates(presence) {
  document.querySelectorAll(".config-drawer").forEach((drawer) => {
    const badge = drawer.querySelector("[data-config-state]");
    if (!badge) return;
    const configured = drawerIsConfigured(drawer, presence);
    badge.dataset.configState = configured ? "configured" : "unset";
    badge.textContent = configured ? "Configured" : "Not configured";
  });
}

export function fillConfiguration() {
  if (!store.snapshot?.operator) return;
  store.filling = true;
  const config = store.snapshot.operator.config;
  const presence = store.snapshot.operator.secrets || {};
  const canFill = (name) => !document.querySelector(`.config-page[data-page="${name}"]`)?.classList.contains("dirty");
  if (canFill("ai")) {
  $("ai-protocol").value = config.ai.protocol;
  $("ai-base").value = config.ai.base_url;
  // A saved profile already carries the list the probe returned when it was
  // applied, so a returning user sees their picker without re-listing.
  if (config.ai.models.length) {
    renderDiscoveredModels(config.ai.models, config.ai.default_model);
  } else {
    clearDiscoveredModels();
  }
  $("ai-vision").value = config.ai.vision_models.join(", ");
  $("ai-private-network").checked = Boolean(config.ai.allow_private_network);
  $("ai-validation").textContent = config.ai.last_validated_at_unix_ms
    ? `Validated ${new Date(config.ai.last_validated_at_unix_ms).toLocaleString()}`
    : "Not validated";
  }
  if (canFill("integrations")) {
  $("composio-enabled").checked = config.integrations.composio_enabled;
  $("google-id").value = config.integrations.google_client_id;
  $("microsoft-id").value = config.integrations.microsoft_client_id;
  $("github-id").value = config.integrations.github_client_id || "";
  $("slack-connector-id").value = config.integrations.slack_client_id || "";
  }
  if (canFill("channels")) {
  $("slack-enabled").checked = config.surfaces.slack_socket_mode;
  $("telegram-enabled").checked = config.surfaces.telegram_polling;
  $("teams-id").value = config.surfaces.teams_app_id;
  $("teams-tenant").value = config.surfaces.teams_tenant_id;
  $("wa-phone").value = config.surfaces.whatsapp_phone_number_id;
  $("wa-waba").value = config.surfaces.whatsapp_waba_id;
  $("resend-domain").value = config.surfaces.resend_inbound_domain;
  }
  secretInputs().forEach((input) => {
    if (!canFill(input.closest(".config-page").dataset.page)) return;
    input.value = "";
    input.dataset.clear = "false";
    input.placeholder = presence[input.dataset.secret] ? "Configured — enter to replace" : "Not configured";
    const button = input.parentElement.querySelector(".secret-clear");
    if (button) {
      button.classList.remove("armed");
      button.textContent = "Remove";
      button.disabled = !presence[input.dataset.secret];
      labelSecretButton(button, input);
    }
  });
  // After the fields, because the badge is read off them.
  paintConfigStates(presence);
  document.querySelectorAll(".config-page").forEach((page) => {
    if (canFill(page.dataset.page)) sectionRevisions.set(page.dataset.page, config.revision);
  });
  store.filling = false;
}

export function collectConfiguration(pageName) {
  const config = structuredClone(store.snapshot.operator.config);
  config.ai = {
    ...config.ai,
    protocol: $("ai-protocol").value,
    base_url: $("ai-base").value.trim(),
    default_model: $("ai-model").value.trim(),
    // Whatever the probe last returned for this endpoint. Apply re-probes and
    // overwrites this with its own list, so it is a hint, not a claim.
    models: discoveredModels,
    vision_models: csv($("ai-vision").value),
    allow_private_network: $("ai-private-network").checked,
  };
  config.integrations = {
    ...config.integrations,
    composio_enabled: $("composio-enabled").checked,
    google_client_id: $("google-id").value.trim(),
    microsoft_client_id: $("microsoft-id").value.trim(),
    github_client_id: $("github-id").value.trim(),
    slack_client_id: $("slack-connector-id").value.trim(),
  };
  config.surfaces = {
    ...config.surfaces,
    slack_socket_mode: $("slack-enabled").checked,
    telegram_polling: $("telegram-enabled").checked,
    teams_app_id: $("teams-id").value.trim(),
    teams_tenant_id: $("teams-tenant").value.trim(),
    whatsapp_phone_number_id: $("wa-phone").value.trim(),
    whatsapp_waba_id: $("wa-waba").value.trim(),
    resend_inbound_domain: $("resend-domain").value.trim(),
  };
  const secrets = {};
  secretInputs().forEach((input) => {
    if (input.closest(".config-page").dataset.page !== pageName) return;
    secrets[input.dataset.secret] = input.dataset.clear === "true" ? { action: "remove" }
      : input.value ? { action: "replace", value: input.value } : { action: "keep" };
  });
  const name = pageName === "channels" ? "surfaces" : pageName;
  return { expected_revision: sectionRevisions.get(pageName), section: { name, value: config[name] }, secrets };
}

export async function saveConfiguration(button) {
  if (!store.snapshot || button.disabled) return false;
  const original = button.textContent;
  const page = button.closest(".config-page");
  const id = nextId("apply");
  let complete;
  const completion = new Promise((resolve) => { complete = resolve; });
  pendingSaves.set(id, { page, button, original, complete, started: Date.now(),
    expectedRevision: sectionRevisions.get(page.dataset.page), version: draftVersions.get(page.dataset.page) || 0 });
  setSectionError(page, "");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await invoke("apply_operator_config", {
      id,
      payload: collectConfiguration(page.dataset.page),
    });
    requestSnapshot();
    return await completion;
  } catch (error) {
    pendingSaves.delete(id);
    button.disabled = false;
    button.textContent = original;
    setSectionError(page, friendlyError(error));
    toast(friendlyError(error), true);
    return false;
  }
}

export function setSectionError(page, message) {
  let error = page.querySelector(".section-error");
  if (!error) {
    error = document.createElement("p");
    error.className = "section-error danger-warning";
    error.setAttribute("role", "alert");
    page.append(error);
  }
  error.textContent = message;
  error.hidden = !message;
}
