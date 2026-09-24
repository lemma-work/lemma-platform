import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import {
    CREDENTIAL_FORMS, LOCAL_SERVER_KEY, addToWorkspace, alreadyInWorkspace, channelLine, credentialFormForChannel,
    detectLocalServers, enablePayload, formConfigured, friendlyError, healthLine, joinPolicyCopy,
    oauthFormForConnector, onLocalWorkspaceOrigin, operatorProvider, postgresMajorChangeMessage, readSnapshot,
    sandboxWording, sectionPayloads, sharingBusy, thisMac, thisMacAvailability, thisMacReachable, updateOffer,
    type AppUpdateStatus, type Installation, type ThisMacSnapshot,
} from "../src/desktop/this-mac.ts";
import { requestedFocus, requestedSection } from "../src/desktop/open-settings.ts";

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

const OWNER: Installation = { deployment: "desktop", is_owner: true, signup_mode: "invite_only" };

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

test("a browser, a hosted workspace, a server and a guest see no This Mac at all", () => {
    // No shell: a browser, or a LAN visitor on a local deployment.
    assert.equal(thisMacAvailability({ bridge: false, localOrigin: true, installation: OWNER, loading: false }), "hidden");
    // A self-hosted or hosted deployment has no owner and no machine to set.
    assert.equal(thisMacAvailability({
        bridge: true, localOrigin: true, installation: { ...OWNER, deployment: "server", is_owner: false }, loading: false,
    }), "hidden");
    // Someone else signed in on the owner's Desktop.
    assert.equal(thisMacAvailability({ bridge: true, localOrigin: true, installation: { ...OWNER, is_owner: false }, loading: false }), "hidden");
    // The installation did not answer at all.
    assert.equal(thisMacAvailability({ bridge: true, localOrigin: true, installation: null, loading: false }), "hidden");
});

test("the owner sees it on this installation's origin, and a note on a shared one", () => {
    assert.equal(thisMacAvailability({ bridge: true, localOrigin: true, installation: OWNER, loading: false }), "shown");
    assert.equal(thisMacAvailability({ bridge: true, localOrigin: false, installation: OWNER, loading: false }), "elsewhere");
    assert.equal(thisMacAvailability({ bridge: true, localOrigin: true, installation: undefined, loading: true }), "pending");
});

test("only the loopback workspace hosts count as this installation's origin", () => {
    page({ hostname: "app.lemma.localhost" });
    assert.equal(onLocalWorkspaceOrigin(), true);
    page({ hostname: "app.127.0.0.1.sslip.io" });
    assert.equal(onLocalWorkspaceOrigin(), true);
    for (const shared of ["192.168.1.20", "example.ngrok.app", "lemma.work", "evil.lemma.localhost"]) {
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

test("the Slack form writes the connector app and the bot to their own sections", () => {
    const payloads = sectionPayloads(snapshot(), "slack", { slack_client_id: "123.456", slack_socket_mode: true }, {
        "integrations.slack_client_secret": { action: "replace", value: "a" },
        "surfaces.slack_bot_token": { action: "replace", value: "xoxb" },
    });
    assert.deepEqual(payloads.map((one) => one.section.name), ["integrations", "surfaces"]);
    assert.deepEqual(Object.keys(payloads[0].secrets), ["integrations.slack_client_secret"]);
    assert.deepEqual(Object.keys(payloads[1].secrets), ["surfaces.slack_bot_token"]);
    assert.equal((payloads[1].section.value as { slack_socket_mode: boolean }).slack_socket_mode, true);
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
    assert.equal(credentialFormForChannel("SLACK"), "slack");
    assert.equal(credentialFormForChannel("resend"), "resend");
    assert.equal(credentialFormForChannel("EMAIL"), "resend");
    assert.equal(credentialFormForChannel("DISCORD"), null);
});

test("opening Settings from a link carries the form to open, and only a plain word", () => {
    const at = (detail: unknown) => ({ detail }) as unknown as Event;
    assert.equal(requestedSection(at({ section: "this-mac-advanced", focus: "google" })), "this-mac-advanced");
    assert.equal(requestedFocus(at({ section: "this-mac-advanced", focus: "google" })), "google");
    assert.equal(requestedFocus(at({ section: "this-mac-advanced", focus: "<img src=x>" })), null);
    assert.equal(requestedFocus(at({ section: "models" })), null);
    // The sections the menu asks for are ones Settings knows.
    for (const section of ["this-mac", "this-mac-sharing", "models"]) assert.equal(requestedSection(at({ section })), section);
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
        operator: { config: { revision: 1, ai: { protocol: "openai_compat", base_url: "http://127.0.0.1:11434/v1", default_model: "qwen3", models: ["llama3.2", "qwen3"] } }, secrets: {} },
    }));
    assert.deepEqual(local, { protocol: "openai", name: "Ollama", baseUrl: "http://127.0.0.1:11434/v1", models: ["qwen3", "llama3.2"], needsKey: false });
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
    const local = { protocol: "openai" as const, name: "Ollama", baseUrl: "http://127.0.0.1:11434/v1", models: ["qwen3"], needsKey: false };
    await addToWorkspace(local, "", add);
    assert.deepEqual(added, [{ protocol: "openai", name: "Ollama", baseUrl: "http://127.0.0.1:11434/v1", apiKey: LOCAL_SERVER_KEY, models: ["qwen3"] }]);

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
