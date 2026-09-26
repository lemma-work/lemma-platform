import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import {
    CREDENTIAL_FORMS, LOCAL_SERVER_KEY, STARTING_PATIENCE_MS, addToWorkspace, healthDetail, stuckStarting, sharingPhaseWords, updateProblem, alreadyInWorkspace, channelLine, credentialFormForChannel,
    detectLocalServers, enablePayload, formConfigured, friendlyError, healthLine, HOST_EXECUTION_CONSEQUENCE,
    hostExecutionRow, joinPolicyCopy,
    oauthFormForConnector, onLocalWorkspaceOrigin, operatorProvider, postgresMajorChangeMessage, readSnapshot,
    sandboxWording, sectionPayloads, sharingBusy, thisMac, thisMacAvailability, thisMacReachable, updateOffer,
    type AppUpdateStatus, type ThisMacSnapshot,
} from "../src/desktop/this-mac.ts";
import { requestedFocus, requestedSection } from "../src/desktop/open-settings.ts";
import { readStatus } from "../src/desktop/agent-host.ts";

/* ── a pretend page ────────────────────────────────────────────────── */

type Call = { command: string; args?: Record<string, unknown> };

/** A `window` shaped like the desktop app's: the shell's `invoke`, the site
 *  config that says which deployment this is, and a hostname. */
function page({
    shell,
    deployment = "local",
    hostname = "app.lemma.localhost",
}: {
    shell?: (command: string, args?: Record<string, unknown>) => unknown;
    deployment?: string;
    hostname?: string;
} = {}): Call[] {
    const calls: Call[] = [];
    const win: Record<string, unknown> = {
        __LEMMA_SITE__: { analyticsKey: "", analyticsHost: "", deployment },
        location: { hostname },
    };
    if (shell) {
        win.__TAURI__ = {
            core: {
                invoke: async (command: string, args?: Record<string, unknown>) => {
                    calls.push({ command, args });
                    return shell(command, args);
                },
            },
        };
    }
    (globalThis as { window?: unknown }).window = win;
    return calls;
}

afterEach(() => {
    delete (globalThis as { window?: unknown }).window;
});

/** A snapshot as the shell sends it, with whatever a test overrides. */
function snapshot(overrides: Record<string, unknown> = {}): ThisMacSnapshot {
    return readSnapshot({
        release: "0.8.0",
        state: { ready: true, running: true, status: "Ready", url: "http://app.lemma.localhost:52413/", api_url: "http://app.lemma.localhost:52414/" },
        services: [{ id: "backend", running: true }, { id: "frontend", running: true }],
        operator: {
            config: {
                revision: 4,
                ai: { protocol: "unconfigured", base_url: "", default_model: "", models: [], vision_models: [] },
                integrations: { composio_enabled: false, google_client_id: "", microsoft_client_id: "", github_client_id: "", slack_client_id: "" },
                surfaces: {
                    slack_socket_mode: false, telegram_polling: false, teams_app_id: "", teams_tenant_id: "",
                    whatsapp_phone_number_id: "", whatsapp_waba_id: "", resend_inbound_domain: "",
                },
            },
            secrets: {},
        },
        sharing: { mode: "this_computer", phase: "ready", who_can_join: "invite_only" },
        sandbox_images: { state: "not-prepared", detail: "" },
        app: { version: "0.8.0", channel: "stable", updates_supported: true, start_at_login: false },
        ...overrides,
    });
}

/* ── who sees it ───────────────────────────────────────────────────── */

test("a browser and a hosted workspace see no This Mac at all", () => {
    // No shell: a browser, or a LAN visitor on a local deployment.
    assert.equal(thisMacAvailability({ bridge: false, localDeployment: true, localOrigin: true }), "hidden");
    // The desktop app signed in to a hosted workspace: no machine to set.
    assert.equal(thisMacAvailability({ bridge: true, localDeployment: false, localOrigin: false }), "hidden");
    assert.equal(thisMacAvailability({ bridge: true, localDeployment: false, localOrigin: null }), "hidden");
});

test("the app's own window sees it on the loopback origin, whoever is signed in", () => {
    // No account enters into it: the rule is the page's origin, as in the shell.
    assert.equal(thisMacAvailability({ bridge: true, localDeployment: true, localOrigin: true }), "shown");
    assert.equal(thisMacAvailability({ bridge: true, localDeployment: true, localOrigin: false }), "elsewhere");
    assert.equal(thisMacAvailability({ bridge: true, localDeployment: true, localOrigin: null }), "pending");
});

test("only the loopback workspace hosts count as this installation's origin", () => {
    page({ hostname: "app.lemma.localhost" });
    assert.equal(onLocalWorkspaceOrigin(), true);
    /* The public loopback wildcard an earlier build served on is not this
       installation any more. */
    for (const shared of ["192.168.1.20", "example.ngrok.app", "lemma.work", "evil.lemma.localhost", "app.127.0.0.1.sslip.io"]) {
        page({ hostname: shared, shell: () => null });
        assert.equal(onLocalWorkspaceOrigin(), false, shared);
        assert.equal(thisMacReachable(), false, shared);
    }
    page({ shell: () => null });
    assert.equal(thisMacReachable(), true);
    // A hosted deployment in the app is not a local one, whatever its host.
    page({ shell: () => null, deployment: "hosted" });
    assert.equal(thisMacReachable(), false);
});

/* ── the commands ──────────────────────────────────────────────────── */

test("each This Mac verb calls exactly one shell command, with the arguments it expects", async () => {
    const calls = page({ shell: (command) => (command === "local_settings_snapshot" ? { release: "0.8.0" } : true) });
    const read = await thisMac.snapshot();
    assert.equal(read.release, "0.8.0");
    await thisMac.sharing("access", { who_can_join: "open" });
    await thisMac.setStartAtLogin(true);
    await thisMac.installUpdate("0.8.1");
    await thisMac.repair();
    assert.deepEqual(calls.map((call) => call.command), [
        "local_settings_snapshot", "local_sharing", "set_start_at_login", "install_app_update", "repair_runtime",
    ]);
    assert.deepEqual(calls[1].args, { action: "access", payload: { who_can_join: "open" } });
    assert.deepEqual(calls[2].args, { enabled: true });
    // The version shown is sent, so the shell can refuse if the feed moved on.
    assert.deepEqual(calls[3].args, { resetData: false, expectedVersion: "0.8.1" });
});

test("a snapshot missing fields reads as not set, not as a crash", () => {
    const empty = readSnapshot({});
    assert.equal(empty.operator.config.ai.protocol, "unconfigured");
    assert.equal(empty.sharing, null);
    assert.deepEqual(empty.services, []);
    assert.equal(empty.app.start_at_login, false);
    const odd = readSnapshot({ sharing: { mode: "sideways", who_can_join: "everyone" } });
    assert.equal(odd.sharing?.mode, "this_computer");
    assert.equal(odd.sharing?.who_can_join, "invite_only");
});

test("a refusal from the shell is said as something a person can act on", () => {
    assert.match(friendlyError(new Error("Command local_sharing not allowed by ACL")), /own window on this computer/);
    assert.match(friendlyError("control endpoint unavailable: No such file"), /background service/);
    assert.equal(friendlyError(new Error("Error: the hostname is taken")), "the hostname is taken");
});

/* ── overview ──────────────────────────────────────────────────────── */

test("the overview says health, version and whether it is current in one line", () => {
    const update: AppUpdateStatus = { channel: "stable", currentVersion: "0.8.0", updatesSupported: true, dataCompatibility: "compatible" };
    assert.equal(healthLine(snapshot(), update), "Running · v0.8.0 · up to date");
    assert.equal(healthLine(snapshot(), { ...update, availableVersion: "0.8.1" }), "Running · v0.8.0 · 0.8.1 available");
    // A build that cannot update itself says nothing about being current.
    assert.equal(healthLine(snapshot(), { ...update, updatesSupported: false }), "Running · v0.8.0");
    const starting = snapshot({ state: { ready: false, running: true }, services: [] });
    assert.equal(healthLine(starting, null), "Starting · v0.8.0");
    const broken = snapshot({ services: [{ id: "backend", running: false, circuit_open: true }] });
    assert.match(healthLine(broken, null), /^Needs attention/);
    const nightly = snapshot({ app: { version: "0.8.1-nightly.4.1", channel: "nightly" } });
    assert.equal(healthLine(nightly, null), "Running · v0.8.1-nightly.4.1 nightly");
});

/* ── coding agents ─────────────────────────────────────────────────── */

test("the sandbox download is offered only where it would do something", () => {
    for (const offered of ["not-prepared", "failed"]) assert.equal(sandboxWording(offered, "this Mac").offer, true, offered);
    for (const quiet of ["ready", "downloading", "unsupported", undefined]) assert.equal(sandboxWording(quiet, "this Mac").offer, false, String(quiet));
    assert.match(sandboxWording("ready", "this PC").text, /this PC/);
});

/* ── sharing ───────────────────────────────────────────────────────── */

test("sharing on the local network needs a network, and never asks for public consent", () => {
    assert.deepEqual(enablePayload({ kind: "lan", interface: "" }), { missing: "Choose the network to share on." });
    assert.deepEqual(enablePayload({ kind: "lan", interface: "192.168.1.20" }), {
        payload: { mode: "local_network", interface: "192.168.1.20", public_warning_confirmed: false },
    });
});

test("a public link never carries the consent flag from this page", () => {
    // The shell asks natively and sets it itself; a page that sent `true`
    // would have agreed on the person's behalf.
    const ngrok = enablePayload({ kind: "public", provider: "ngrok", cloudflareSetup: "automatic", hostname: "", tunnelId: "", tunnelName: "" });
    assert.ok("payload" in ngrok);
    assert.equal("public_warning_confirmed" in ngrok.payload, false);
    assert.deepEqual(ngrok.payload, { mode: "public", provider: "ngrok" });
});

test("Cloudflare defaults to automatic setup and asks only for what is missing", () => {
    assert.deepEqual(
        enablePayload({ kind: "public", provider: "cloudflare", cloudflareSetup: "automatic", hostname: " ", tunnelId: "", tunnelName: "" }),
        { missing: "Enter the public hostname to create in your Cloudflare zone." },
    );
    assert.deepEqual(
        enablePayload({ kind: "public", provider: "cloudflare", cloudflareSetup: "automatic", hostname: "lemma.example.com", tunnelId: "", tunnelName: "" }),
        { payload: { mode: "public", provider: "cloudflare", cloudflare_setup: "automatic", hostname: "lemma.example.com" } },
    );
    assert.deepEqual(
        enablePayload({ kind: "public", provider: "cloudflare", cloudflareSetup: "existing", hostname: "lemma.example.com", tunnelId: "", tunnelName: "" }),
        { missing: "Choose one of your named tunnels." },
    );
    const existing = enablePayload({
        kind: "public", provider: "cloudflare", cloudflareSetup: "existing", hostname: "lemma.example.com", tunnelId: "abc", tunnelName: "home",
    });
    assert.ok("payload" in existing);
    assert.equal(existing.payload.cloudflare_tunnel_id, "abc");
    assert.equal(existing.payload.cloudflare_tunnel_name, "home");
});

test("who can join is said for the mode in force, invite-only by default", () => {
    assert.match(joinPolicyCopy("invite_only", "public"), /Only people you invite can create an account/);
    assert.match(joinPolicyCopy("open", "public"), /Anyone with the link can create an account/);
    assert.match(joinPolicyCopy("open", "local_network"), /Anyone on this network/);
    assert.match(joinPolicyCopy("invite_only", "this_computer"), /^Once shared/);
    assert.equal(readSnapshot({ sharing: {} }).sharing?.who_can_join, "invite_only");
});

test("a sharing change is busy until the daemon says it is ready or failed", () => {
    assert.equal(sharingBusy(null), false);
    assert.equal(sharingBusy(snapshot().sharing), false);
    assert.equal(sharingBusy(readSnapshot({ sharing: { phase: "starting_gateway" } }).sharing), true);
    assert.equal(sharingBusy(readSnapshot({ sharing: { phase: "ready", transition_running: true } }).sharing), true);
    assert.equal(sharingBusy(readSnapshot({ sharing: { phase: "error" } }).sharing), false);
});

/* ── updates ───────────────────────────────────────────────────────── */

test("an update that changes the Postgres major is shown but cannot be installed", () => {
    const blocked: AppUpdateStatus = {
        channel: "stable", currentVersion: "0.8.0", updatesSupported: true, availableVersion: "0.9.0",
        dataCompatibility: "postgres-major-change", installedPostgresMajor: 16, candidatePostgresMajor: 17,
    };
    const offer = updateOffer(blocked);
    assert.equal(offer.blocked, postgresMajorChangeMessage(blocked));
    assert.match(offer.blocked!, /from Postgres 16 to Postgres 17/);
    const fine = updateOffer({ ...blocked, dataCompatibility: "compatible", runtimeDownloadBytes: 900 * 1024 * 1024 });
    assert.equal(fine.blocked, null);
    assert.match(fine.cost, /about 900 MB/);
    assert.deepEqual(updateOffer(null), { blocked: null, cost: "" });
});

test("the channel is described, not offered as a switch", () => {
    assert.match(channelLine(null, "nightly"), /don’t update themselves/);
    assert.match(channelLine(null, "stable"), /separate download/);
    assert.match(channelLine(null, "dev"), /development build/);
});

/* ── advanced: developer credentials ──────────────────────────────── */

test("a form is set up when anything in it is, secrets by presence alone", () => {
    const base = snapshot();
    assert.equal(formConfigured(base, "google"), false);
    const withId = snapshot({
        operator: { config: { revision: 4, integrations: { google_client_id: "id.apps" } }, secrets: {} },
    });
    assert.equal(formConfigured(withId, "google"), true);
    const withSecret = snapshot({ operator: { config: { revision: 4 }, secrets: { "surfaces.telegram_bot_token": true } } });
    assert.equal(formConfigured(withSecret, "telegram"), true);
    assert.equal(formConfigured(withSecret, "slack"), false);
});

test("saving a form sends its whole section, only its own secrets, and nothing it did not change", () => {
    const base = snapshot();
    // Nothing typed: nothing sent.
    assert.deepEqual(sectionPayloads(base, "google", { google_client_id: "" }, {}), []);
    const [google] = sectionPayloads(base, "google", { google_client_id: " id.apps " }, {
        "integrations.google_client_secret": { action: "replace", value: " s3cret " },
    });
    assert.equal(google.section.name, "integrations");
    assert.equal(google.expected_revision, 4);
    // The whole section, because the daemon replaces a section rather than
    // merging into it.
    assert.deepEqual(Object.keys(google.section.value).sort(), Object.keys(base.operator.config.integrations).sort());
    assert.equal((google.section.value as { google_client_id: string }).google_client_id, "id.apps");
    assert.deepEqual(google.secrets, { "integrations.google_client_secret": { action: "replace", value: "s3cret" } });
});

test("a secret typed and cleared is kept; removing one is its own act", () => {
    const base = snapshot({ operator: { config: { revision: 4 }, secrets: { "surfaces.telegram_bot_token": true } } });
    assert.deepEqual(sectionPayloads(base, "telegram", { telegram_polling: false }, {
        "surfaces.telegram_bot_token": { action: "replace", value: "  " },
    }), []);
    const [removal] = sectionPayloads(base, "telegram", { telegram_polling: false }, {
        "surfaces.telegram_bot_token": { action: "remove" },
    });
    assert.deepEqual(removal.secrets, { "surfaces.telegram_bot_token": { action: "remove" } });
});

test("Slack's connector app and its bot are separate forms, each in its own section", () => {
    const [app] = sectionPayloads(snapshot(), "slack-app", { slack_client_id: "123.456" }, {
        "integrations.slack_client_secret": { action: "replace", value: "a" },
    });
    assert.equal(app.section.name, "integrations");
    assert.deepEqual(Object.keys(app.secrets), ["integrations.slack_client_secret"]);
    const [bot] = sectionPayloads(snapshot(), "slack", {}, {
        "surfaces.slack_app_token": { action: "replace", value: "xapp" },
    });
    assert.equal(bot.section.name, "surfaces");
    assert.deepEqual(Object.keys(bot.secrets), ["surfaces.slack_app_token"]);
});

test("Composio is on exactly while it has a key", () => {
    const [saving] = sectionPayloads(snapshot(), "composio", {}, {
        "integrations.composio_api_key": { action: "replace", value: "ck" },
    });
    assert.equal((saving.section.value as { composio_enabled: boolean }).composio_enabled, true);
    const withKey = snapshot({
        operator: { config: { revision: 4, integrations: { composio_enabled: true } }, secrets: { "integrations.composio_api_key": true } },
    });
    const [removing] = sectionPayloads(withKey, "composio", {}, { "integrations.composio_api_key": { action: "remove" } });
    assert.equal((removing.section.value as { composio_enabled: boolean }).composio_enabled, false);
    // Nothing typed on a stored key: nothing changes.
    assert.deepEqual(sectionPayloads(withKey, "composio", {}, {}), []);
});

test("every form's fields are ones the daemon's sections actually have", () => {
    const base = snapshot();
    for (const spec of CREDENTIAL_FORMS) {
        for (const field of spec.fields) {
            if (field.secret) {
                assert.match(field.key, /^(integrations|surfaces)\.[a-z_]+$/, field.key);
            } else {
                assert.ok(field.key in base.operator.config.integrations || field.key in base.operator.config.surfaces, field.key);
            }
        }
    }
});

/* ── point of need ─────────────────────────────────────────────────── */

test("connectors and channels map to the form that sets them up here", () => {
    assert.equal(oauthFormForConnector("gmail"), "google");
    assert.equal(oauthFormForConnector("google_calendar"), "google");
    assert.equal(oauthFormForConnector("github"), "github");
    assert.equal(oauthFormForConnector("outlook"), "microsoft");
    assert.equal(oauthFormForConnector("notion"), null);
    assert.equal(oauthFormForConnector("slack"), "slack-app");
    assert.equal(oauthFormForConnector("microsoft_teams"), "teams");
    assert.equal(credentialFormForChannel("SLACK"), "slack");
    assert.equal(credentialFormForChannel("resend"), "resend");
    assert.equal(credentialFormForChannel("EMAIL"), "resend");
    assert.equal(credentialFormForChannel("DISCORD"), null);
});

test("opening Settings from a link carries the form to open, and only a plain word", () => {
    const at = (detail: unknown) => ({ detail }) as unknown as Event;
    assert.equal(requestedSection(at({ section: "this-mac-advanced", focus: "google" })), "this-mac-advanced");
    assert.equal(requestedSection(at({ section: "this-mac-setup", focus: "ai" })), "this-mac-setup");
    assert.equal(requestedFocus(at({ section: "this-mac-advanced", focus: "google" })), "google");
    assert.equal(requestedFocus(at({ section: "this-mac-advanced", focus: "<img src=x>" })), null);
    assert.equal(requestedFocus(at({ section: "models" })), null);
    // The sections the menu asks for are ones Settings knows.
    for (const section of ["this-mac", "this-mac-setup", "this-mac-sharing", "models"]) assert.equal(requestedSection(at({ section })), section);
});

/* ── models ────────────────────────────────────────────────────────── */

test("local model servers are found by asking them, never with the stored key", async () => {
    const calls = page({
        shell: (_command, args) => {
            const url = ((args?.payload as { ai: { base_url: string } }).ai.base_url);
            if (url.includes("11434")) return ["llama3.2", "qwen3"];
            if (url.includes("1234")) throw new Error("connection refused");
            return [];
        },
    });
    const found = await detectLocalServers();
    assert.deepEqual(found.map((one) => [one.id, one.models]), [["ollama", ["llama3.2", "qwen3"]]]);
    assert.ok(calls.every((call) => call.command === "discover_provider_models"));
    // Omitted, the shell would attach the operator's key to this endpoint.
    assert.ok(calls.every((call) => (call.args?.payload as { api_key?: string }).api_key === ""));
});

test("a server that answers with no models is not suggested", async () => {
    const found = await detectLocalServers(async () => []);
    assert.deepEqual(found, []);
});

test("nothing already in the organization is suggested again", () => {
    const runtimes = [{ baseUrl: "http://127.0.0.1:11434/v1/", archived: false }, { baseUrl: "https://api.openai.com/v1", archived: true }];
    assert.equal(alreadyInWorkspace("http://127.0.0.1:11434/v1", runtimes), true);
    // A retired one is not "already there".
    assert.equal(alreadyInWorkspace("https://api.openai.com/v1", runtimes), false);
    assert.equal(alreadyInWorkspace("http://127.0.0.1:1234/v1", runtimes), false);
});

test("the provider set on this computer is offered with its models, and a key only where it needs one", () => {
    assert.equal(operatorProvider(snapshot()), null);
    const local = operatorProvider(snapshot({
        operator: { config: { revision: 1, ai: { protocol: "openai_compat", base_url: "http://127.0.0.1:11434/v1", default_model: "qwen3", models: ["llama3.2", "qwen3"], vision_models: ["llama3.2"] } }, secrets: {} },
    }));
    assert.deepEqual(local, {
        protocol: "openai", name: "Ollama", baseUrl: "http://127.0.0.1:11434/v1", models: ["qwen3", "llama3.2"], visionModels: ["llama3.2"], needsKey: false,
    });
    const keyed = operatorProvider(snapshot({
        operator: { config: { revision: 1, ai: { protocol: "anthropic_compat", base_url: "https://api.anthropic.com", default_model: "claude-x", models: [] } }, secrets: { "ai.api_key": true } },
    }));
    assert.equal(keyed?.protocol, "anthropic");
    assert.equal(keyed?.name, "anthropic.com");
    assert.equal(keyed?.needsKey, true);
});

test("adding it to the workspace creates the provider and leaves this computer's fallback alone", async () => {
    const added: unknown[] = [];
    const add = async (key: unknown) => { added.push(key); };
    const local = { protocol: "openai" as const, name: "Ollama", baseUrl: "http://127.0.0.1:11434/v1", models: ["qwen3", "llava"], visionModels: ["llava"], needsKey: false };
    await addToWorkspace(local, "", add);
    // The models that read images go with it, rather than arriving text-only.
    assert.deepEqual(added, [{
        protocol: "openai", name: "Ollama", baseUrl: "http://127.0.0.1:11434/v1", apiKey: LOCAL_SERVER_KEY, models: ["qwen3", "llava"], visionModels: ["llava"],
    }]);

    // A keyed provider cannot be moved without its key, which the page never
    // learns: it is asked for, and nothing is created until it is given.
    const keyed = { ...local, name: "OpenAI", baseUrl: "https://api.openai.com/v1", needsKey: true };
    await assert.rejects(addToWorkspace(keyed, "  ", add), /Enter the API key for OpenAI/);
    assert.equal(added.length, 1);
    await addToWorkspace(keyed, " sk-1 ", add);
    assert.equal((added[1] as { apiKey: string }).apiKey, "sk-1");

    // Nothing here clears the operator profile: it is the backend's
    // `system:lemma` fallback for titles, summaries and pods with no default.
    const calls = page({ shell: () => true });
    await addToWorkspace(local, "", add);
    assert.deepEqual(calls, []);
});

/* ── run commands on this Mac ──────────────────────────────────────── */

test("the host-execution switch reflects the Agent Host and is off-limits where nothing can confine commands", () => {
    const on = hostExecutionRow({ host_execution: { enabled: true, available: true } });
    assert.deepEqual(on, { checked: true, blocked: null, consequence: HOST_EXECUTION_CONSEQUENCE });
    assert.match(on.consequence, /inside a sandbox/);
    assert.match(on.consequence, /Teammates’ runs stay in the VM/);
    assert.equal(hostExecutionRow({ host_execution: { enabled: false, available: true } }).checked, false);
    // Not macOS: disabled, with the reason, whatever the setting says.
    const unavailable = hostExecutionRow({ host_execution: { enabled: true, available: false } });
    assert.equal(unavailable.checked, false);
    assert.match(unavailable.blocked ?? "", /macOS/);
    // No status yet, or a shell too old to report it: never shown as on.
    assert.equal(hostExecutionRow(null).checked, false);
    assert.notEqual(hostExecutionRow(null).blocked, null);
    assert.notEqual(hostExecutionRow({ host_execution: null }).blocked, null);
});

test("the Agent Host status carries host execution, and an older shell's does not break it", () => {
    const status = readStatus({ available: true, host_execution: { enabled: true, available: true } });
    assert.deepEqual(status?.host_execution, { enabled: true, available: true });
    assert.equal(readStatus({ available: true })?.host_execution, null);
    assert.deepEqual(readStatus({ available: true, host_execution: { enabled: "yes" } })?.host_execution,
        { enabled: false, available: false });
});

test("turning host execution on sends one boolean to one shell command", async () => {
    const calls = page({ shell: () => ({ available: true, host_execution: { enabled: true, available: true } }) });
    await thisMac.setHostExecution(true);
    await thisMac.setHostExecution(false);
    assert.deepEqual(calls, [
        { command: "set_host_execution", args: { enabled: true } },
        { command: "set_host_execution", args: { enabled: false } },
    ]);
});

/* ── overview and updates, in words ───────────────────────────────── */

test("needs attention says what stopped", () => {
    const stopped = snapshot({ services: [{ id: "backend", running: false, circuit_open: true }, { id: "frontend", running: true }] });
    assert.match(healthDetail(stopped)!, /server kept stopping/);
    const failed = snapshot({ state: { ready: false, running: false, last_error: "the VM would not boot" } });
    assert.equal(healthDetail(failed), "the VM would not boot");
    assert.equal(healthDetail(snapshot()), null);
});

test("an update that cannot be installed yet is not announced as available", () => {
    const blocked: AppUpdateStatus = {
        channel: "stable", currentVersion: "0.8.0", updatesSupported: true, availableVersion: "0.9.0",
        dataCompatibility: "postgres-major-change", installedPostgresMajor: 16, candidatePostgresMajor: 17,
    };
    assert.doesNotMatch(healthLine(snapshot(), blocked), /available/);
    assert.match(healthLine(snapshot(), { ...blocked, dataCompatibility: "same" }), /0\.9\.0 available/);
});

test("a declined install is a choice, and a failed check is the network's", () => {
    assert.deepEqual(updateProblem(new Error("The update was not installed.")), { text: "Not installed. Lemma is still on this version.", neutral: true });
    assert.equal(updateProblem(new Error("could not download the update: dns error")).neutral, false);
    assert.match(updateProblem(new Error("could not download the update: dns error")).text, /update server/);
});

test("sharing phases are said in words, never as keys", () => {
    assert.equal(sharingPhaseWords("starting_tunnel"), "Opening the public link");
    assert.equal(sharingPhaseWords("something_new"), "Working");
});

test("a start that never finishes stops being called one", () => {
    const starting = snapshot({ state: { ready: false, running: true }, services: [{ id: "backend", running: false }, { id: "frontend", running: true }] });
    assert.equal(stuckStarting(starting, null, 0), null);
    assert.equal(stuckStarting(starting, 0, STARTING_PATIENCE_MS - 1), null);
    assert.match(stuckStarting(starting, 0, STARTING_PATIENCE_MS)!, /server isn’t running yet/);
    assert.equal(stuckStarting(snapshot(), 0, STARTING_PATIENCE_MS * 2), null, "running is not starting");
});

test("a build without the Agent Host says so instead of waiting for it", () => {
    assert.match(hostExecutionRow({ available: false, host_execution: null }).blocked!, /doesn’t include the Agent Host/);
    assert.match(channelLine(null, "unknown"), /couldn’t tell/);
});

test("startup warnings are read from the snapshot and said with a next step", async () => {
    const { readStartupWarnings, startupWarningLine } = await import("../src/desktop/this-mac.ts");
    const snapshot = readSnapshot({
        warnings: [
            { code: "update-interrupted", message: "Your last update didn't finish. Install Lemma 0.9.0 to continue — don't reopen the older version.", version: "0.9.0" },
            { code: "settings-writes-disabled", message: "Quit and reopen Lemma." },
            { code: "startup-repaired", message: "   " },
            "not an object",
        ],
    });
    assert.deepEqual(snapshot.warnings?.map((warning) => [warning.code, warning.version]), [
        ["update-interrupted", "0.9.0"],
        ["settings-writes-disabled", null],
    ]);
    const [update, settings] = snapshot.warnings!;
    assert.equal(startupWarningLine(update).next, "updates");
    assert.equal(startupWarningLine(settings).next, "logs");
    assert.equal(startupWarningLine({ code: "something-new", message: "x", version: null }).next, "logs");
    // An older shell sends none; the page reads that as nothing to say.
    assert.deepEqual(readSnapshot({}).warnings, []);
    assert.deepEqual(readStartupWarnings(null), []);
});
