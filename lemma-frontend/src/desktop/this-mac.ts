import { desktopBridgeAvailable, invoke } from "./bridge";

/** This computer's own settings, as Settings → This Mac reads and writes them.
 *
 *  Everything here reaches the shell through `invoke`, and every command it
 *  names refuses in Rust unless the caller is this installation's own
 *  workspace on its loopback origin (`desktop/src/workspace_settings.rs`). So
 *  this file decides what to *show*; it is never what decides who may act.
 *
 *  Pure where it can be. The React panels in `this-mac-*.tsx` are thin over
 *  these, and `tests/this-mac.test.ts` drives these with a pretend shell. */

/* ── who sees it ───────────────────────────────────────────────────── */

/** The loopback hosts a local install serves its workspace on.
 *
 *  Kept in step with `TRUSTED_LOCAL_BASES` in `desktop/src/main.rs` and the
 *  remote URLs in `desktop/capabilities/workspace.json`. A page anywhere else
 *  -- the LAN address or tunnel host sharing moves this window to -- is one
 *  the shell will not answer, whatever it asks. */
const LOCAL_WORKSPACE_HOSTS = ["app.lemma.localhost", "app.127.0.0.1.sslip.io"];

export function onLocalWorkspaceOrigin(): boolean {
    if (typeof window === "undefined") return false;
    return LOCAL_WORKSPACE_HOSTS.includes(window.location?.hostname ?? "");
}

/** What the This Mac group should be, on this page.
 *
 *  There is no account check. Who may change this computer is decided by where
 *  the page is, not who is signed in: the desktop app's own window, on this
 *  installation's loopback origin, is the person at this Mac -- the same rule
 *  the shell enforces on every command (`workspace_settings.rs`).
 *
 *  - `hidden`: a browser, a hosted workspace, or anything that is not a local
 *    install. Nothing is drawn, not even a hint: a machine's settings are not
 *    a thing a visitor should learn exist.
 *  - `elsewhere`: in the app, on a local install, but on a shared origin. The
 *    shell will not answer here, so the group says where the settings are
 *    instead of offering controls that would fail.
 *  - `shown`: in the app, on this installation's own loopback origin.
 *  - `pending`: the origin has not been read yet (it is read after mount). */
export type ThisMacAvailability = "hidden" | "pending" | "elsewhere" | "shown";

export function thisMacAvailability({
    bridge,
    localDeployment,
    localOrigin,
}: {
    bridge: boolean;
    localDeployment: boolean;
    localOrigin: boolean | null;
}): ThisMacAvailability {
    if (!bridge || !localDeployment) return "hidden";
    if (localOrigin === null) return "pending";
    return localOrigin ? "shown" : "elsewhere";
}

/* ── the snapshot ──────────────────────────────────────────────────── */

export interface IntegrationConfig {
    composio_enabled: boolean;
    google_client_id: string;
    microsoft_client_id: string;
    github_client_id: string;
    slack_client_id: string;
}

export interface SurfaceConfig {
    slack_socket_mode: boolean;
    telegram_polling: boolean;
    teams_app_id: string;
    teams_tenant_id: string;
    whatsapp_phone_number_id: string;
    whatsapp_waba_id: string;
    resend_inbound_domain: string;
}

export interface OperatorAi {
    protocol: string;
    base_url: string;
    default_model: string;
    models: string[];
    vision_models: string[];
    allow_private_network: boolean;
    last_validated_at_unix_ms: number | null;
}

export type SharingMode = "this_computer" | "local_network" | "public";
export type WhoCanJoin = "invite_only" | "open";
export type TunnelProvider = "ngrok" | "cloudflare";

export interface ProviderReadiness {
    installed: boolean;
    authenticated: boolean;
    version: string | null;
    message: string | null;
    instructions: string[];
    tunnels: { id: string; name: string }[];
}

export interface Sharing {
    mode: SharingMode;
    phase: string;
    canonical_url: string;
    provider: TunnelProvider | null;
    provider_readiness: Partial<Record<TunnelProvider, ProviderReadiness>>;
    warnings: string[];
    last_error: string | null;
    interfaces: { name: string; address: string; label: string }[];
    selected_interface: string | null;
    qr_svg: string | null;
    transition_running: boolean;
    who_can_join: WhoCanJoin;
    public_confirmation: string;
    apps_limitation: string;
    preferences: {
        cloudflare_setup?: "automatic" | "existing";
        cloudflare_hostname?: string | null;
        cloudflare_tunnel_id?: string | null;
        cloudflare_tunnel_owned?: boolean;
        cloudflare_tunnel_name?: string | null;
        selected_interface?: string | null;
        last_provider?: TunnelProvider | null;
    };
}

export interface ThisMacSnapshot {
    release: string | null;
    state: { ready: boolean; running: boolean; status: string; last_error: string | null; url: string; api_url: string };
    services: { id: string; running: boolean; circuit_open?: boolean }[];
    operator: {
        config: { revision: number; ai: OperatorAi; integrations: IntegrationConfig; surfaces: SurfaceConfig };
        secrets: Record<string, boolean>;
        readiness: Record<string, string>;
    };
    sharing: Sharing | null;
    sandbox_images: { state: string; detail: string } | null;
    paths: { locald: string; logs: string } | null;
    app: { version: string; channel: string; updates_supported: boolean; start_at_login: boolean };
}

const record = (value: unknown): Record<string, unknown> =>
    value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const text = (value: unknown, fallback = ""): string => (typeof value === "string" ? value : fallback);
const flag = (value: unknown): boolean => value === true;
const strings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((one) => typeof one === "string") : []);

/** Narrow the shell's loose JSON. The Rust side is free to add fields; what
 *  this page reads has a default, so a missing one degrades to "not set"
 *  rather than a crash inside Settings. */
export function readSnapshot(payload: unknown): ThisMacSnapshot {
    const raw = record(payload);
    const state = record(raw.state);
    const operator = record(raw.operator);
    const config = record(operator.config);
    const ai = record(config.ai);
    const integrations = record(config.integrations);
    const surfaces = record(config.surfaces);
    const app = record(raw.app);
    const sharing = raw.sharing && typeof raw.sharing === "object" ? readSharing(raw.sharing) : null;
    const images = record(raw.sandbox_images);
    const paths = record(raw.paths);
    return {
        release: typeof raw.release === "string" ? raw.release : null,
        state: {
            ready: flag(state.ready),
            running: flag(state.running),
            status: text(state.status),
            last_error: typeof state.last_error === "string" && state.last_error ? state.last_error : null,
            url: text(state.url),
            api_url: text(state.api_url),
        },
        services: (Array.isArray(raw.services) ? raw.services : []).map((one) => {
            const service = record(one);
            return { id: text(service.id), running: flag(service.running), circuit_open: flag(service.circuit_open) };
        }),
        operator: {
            config: {
                revision: typeof config.revision === "number" ? config.revision : 0,
                ai: {
                    protocol: text(ai.protocol, "unconfigured"),
                    base_url: text(ai.base_url),
                    default_model: text(ai.default_model),
                    models: strings(ai.models),
                    vision_models: strings(ai.vision_models),
                    allow_private_network: flag(ai.allow_private_network),
                    last_validated_at_unix_ms: typeof ai.last_validated_at_unix_ms === "number" ? ai.last_validated_at_unix_ms : null,
                },
                integrations: {
                    composio_enabled: flag(integrations.composio_enabled),
                    google_client_id: text(integrations.google_client_id),
                    microsoft_client_id: text(integrations.microsoft_client_id),
                    github_client_id: text(integrations.github_client_id),
                    slack_client_id: text(integrations.slack_client_id),
                },
                surfaces: {
                    slack_socket_mode: flag(surfaces.slack_socket_mode),
                    telegram_polling: flag(surfaces.telegram_polling),
                    teams_app_id: text(surfaces.teams_app_id),
                    teams_tenant_id: text(surfaces.teams_tenant_id),
                    whatsapp_phone_number_id: text(surfaces.whatsapp_phone_number_id),
                    whatsapp_waba_id: text(surfaces.whatsapp_waba_id),
                    resend_inbound_domain: text(surfaces.resend_inbound_domain),
                },
            },
            secrets: Object.fromEntries(Object.entries(record(operator.secrets)).map(([key, value]) => [key, value === true])),
            readiness: Object.fromEntries(Object.entries(record(operator.readiness)).map(([key, value]) => [key, text(value)])),
        },
        sharing,
        sandbox_images: raw.sandbox_images ? { state: text(images.state, "unknown"), detail: text(images.detail) } : null,
        paths: raw.paths ? { locald: text(paths.locald), logs: text(paths.logs) } : null,
        app: {
            version: text(app.version),
            channel: text(app.channel, "dev"),
            updates_supported: flag(app.updates_supported),
            start_at_login: flag(app.start_at_login),
        },
    };
}

export function readSharing(payload: unknown): Sharing {
    const raw = record(payload);
    const readiness = record(raw.provider_readiness);
    const provider = (value: unknown): ProviderReadiness => {
        const one = record(value);
        return {
            installed: flag(one.installed),
            authenticated: flag(one.authenticated),
            version: typeof one.version === "string" ? one.version : null,
            message: typeof one.message === "string" ? one.message : null,
            instructions: strings(one.instructions),
            tunnels: (Array.isArray(one.tunnels) ? one.tunnels : []).map((tunnel) => {
                const t = record(tunnel);
                return { id: text(t.id), name: text(t.name) };
            }),
        };
    };
    const mode = text(raw.mode, "this_computer");
    return {
        mode: mode === "local_network" || mode === "public" ? mode : "this_computer",
        phase: text(raw.phase, "ready"),
        canonical_url: text(raw.canonical_url),
        provider: raw.provider === "ngrok" || raw.provider === "cloudflare" ? raw.provider : null,
        provider_readiness: {
            ...(readiness.ngrok ? { ngrok: provider(readiness.ngrok) } : {}),
            ...(readiness.cloudflare ? { cloudflare: provider(readiness.cloudflare) } : {}),
        },
        warnings: strings(raw.warnings),
        last_error: typeof raw.last_error === "string" && raw.last_error ? raw.last_error : null,
        interfaces: (Array.isArray(raw.interfaces) ? raw.interfaces : []).map((one) => {
            const item = record(one);
            return { name: text(item.name), address: text(item.address), label: text(item.label) };
        }),
        selected_interface: typeof raw.selected_interface === "string" ? raw.selected_interface : null,
        qr_svg: typeof raw.qr_svg === "string" && raw.qr_svg ? raw.qr_svg : null,
        transition_running: flag(raw.transition_running),
        who_can_join: raw.who_can_join === "open" ? "open" : "invite_only",
        public_confirmation: text(raw.public_confirmation),
        apps_limitation: text(raw.apps_limitation),
        preferences: record(raw.preferences) as Sharing["preferences"],
    };
}

/* ── the commands ──────────────────────────────────────────────────── */

/** What This Mac may ask of the shell. Every one of these is in
 *  `WORKSPACE_COMMANDS`, granted in `workspace.json`, and caller-checked. */
export const thisMac = {
    snapshot: async () => readSnapshot(await invoke("local_settings_snapshot")),
    applySection: (payload: SectionPayload) => invoke("apply_local_settings", { payload }),
    sharing: (action: "snapshot" | "preflight" | "enable" | "disable" | "access", payload?: Record<string, unknown>) =>
        invoke<{ cancelled?: boolean; event?: string; sharing?: unknown; preflight?: unknown }>("local_sharing", { action, payload }),
    setStartAtLogin: (enabled: boolean) => invoke<boolean>("set_start_at_login", { enabled }),
    /** Answers with the Agent Host's fresh status. */
    setHostExecution: (enabled: boolean) => invoke<unknown>("set_host_execution", { enabled }),
    /** True when it ran, false when the native confirmation was declined. */
    repair: () => invoke<boolean>("repair_runtime"),
    openLogs: () => invoke("open_logs"),
    prepareSandbox: () => invoke("prepare_sandbox_image", { id: "this-mac-" + Date.now() }),
    checkUpdate: () => invoke<AppUpdateStatus>("check_for_app_update"),
    installUpdate: (expectedVersion: string) => invoke("install_app_update", { resetData: false, expectedVersion }),
    telemetryStatus: () => invoke<{ available?: boolean; enabled?: boolean; host?: string; install_id?: string }>("telemetry_status"),
    setTelemetry: (enabled: boolean) => invoke("set_telemetry_enabled", { enabled }),
    diagnosticLogs: (source: string | null, cursor: string | null) =>
        invoke<{ entries?: string; nextCursor?: string | null; sources?: { id: string; label: string }[] }>("diagnostic_logs", { source, cursor }),
    discoverModels: (payload: Record<string, unknown>) => invoke<unknown>("discover_provider_models", { payload }),
};

/* ── run commands on this Mac ──────────────────────────────────────── */

export const HOST_EXECUTION_CONSEQUENCE =
    "Commands run on your Mac inside a sandbox: they can read most files, write only to the conversation folder and caches, and use your gh/git logins. Teammates’ runs stay in the VM.";

export interface HostExecutionRow {
    checked: boolean;
    /** Why the switch cannot be used, or null when it can. */
    blocked: string | null;
    consequence: string;
}

/** The "Run commands on this Mac" switch, from the Agent Host's status.
 *  Pure, so what it says in each state is tested without a page. */
export function hostExecutionRow(
    status: { host_execution: { enabled: boolean; available: boolean } | null } | null,
): HostExecutionRow {
    const setting = status?.host_execution ?? null;
    if (!setting) {
        return { checked: false, blocked: "Waiting for this computer’s Agent Host…", consequence: HOST_EXECUTION_CONSEQUENCE };
    }
    if (!setting.available) {
        return {
            checked: false,
            blocked: "Only available on macOS, which can confine commands in a sandbox. Commands run in the VM.",
            consequence: HOST_EXECUTION_CONSEQUENCE,
        };
    }
    return { checked: setting.enabled, blocked: null, consequence: HOST_EXECUTION_CONSEQUENCE };
}

/** Whether This Mac's commands make sense at all right now. */
export function thisMacReachable(): boolean {
    return desktopBridgeAvailable() && onLocalWorkspaceOrigin();
}

/* ── overview ──────────────────────────────────────────────────────── */

export interface AppUpdateStatus {
    channel: string;
    currentVersion: string;
    buildCommit?: string | null;
    updatesSupported: boolean;
    availableVersion?: string | null;
    runtimeDownloadBytes?: number | null;
    dataCompatibility: string;
    installedPostgresMajor?: number | null;
    candidatePostgresMajor?: number | null;
}

export function healthState(snapshot: ThisMacSnapshot): "running" | "starting" | "attention" | "stopped" {
    const services = snapshot.services;
    if (snapshot.state.last_error && !snapshot.state.ready) return "attention";
    if (services.some((service) => service.circuit_open)) return "attention";
    if (snapshot.state.ready && services.length > 0 && services.every((service) => service.running)) return "running";
    return snapshot.state.running ? "starting" : "stopped";
}

const HEALTH_WORDS = { running: "Running", starting: "Starting", attention: "Needs attention", stopped: "Stopped" } as const;

/** One line: "Running · v0.8.0 · up to date". The update part only when
 *  there is something true to say; a build that cannot update itself says
 *  nothing about being current. */
export function healthLine(snapshot: ThisMacSnapshot, update: AppUpdateStatus | null): string {
    const parts: string[] = [HEALTH_WORDS[healthState(snapshot)]];
    const version = update?.currentVersion || snapshot.app.version;
    if (version) {
        const channel = snapshot.app.channel && snapshot.app.channel !== "stable" ? " " + snapshot.app.channel : "";
        parts.push("v" + version + channel);
    }
    if (update?.updatesSupported) parts.push(update.availableVersion ? update.availableVersion + " available" : "up to date");
    return parts.join(" · ");
}

/* ── coding agents: the sandbox image ──────────────────────────────── */

/** What the sandbox row says, and whether the download is worth offering.
 *  `not-prepared` and `failed` are the only states where the button does
 *  anything useful. */
export function sandboxWording(state: string | null | undefined, noun: string): { text: string; offer: boolean } {
    switch (state) {
        case "ready":
            return { text: `Downloaded. Pods can run code, shells and browsers on ${noun}.`, offer: false };
        case "downloading":
            return { text: "Downloading…", offer: false };
        case "failed":
            return { text: "The last download did not finish. The first task that needs it will try again, or try now.", offer: true };
        case "unsupported":
            return { text: "This installation runs no private runtime, so there is nothing to download.", offer: false };
        case "not-prepared":
            return { text: "Not downloaded. Coding agents do not need it; pods need it to run code, shells and browsers.", offer: true };
        default:
            return { text: "Checking…", offer: false };
    }
}

/* ── sharing ───────────────────────────────────────────────────────── */

export function sharingModeName(mode: SharingMode, noun: string): string {
    if (mode === "local_network") return "Local network";
    if (mode === "public") return "Public";
    return noun.charAt(0).toUpperCase() + noun.slice(1);
}

/** One line of consequence per choice. */
export function sharingModeConsequence(mode: SharingMode, noun: string): string {
    if (mode === "local_network") return "Phones and laptops on this Wi-Fi can open Lemma. Use it only on a network you trust.";
    if (mode === "public") return "Anyone with the link can reach the sign-in page, through your ngrok or Cloudflare account.";
    return `Only ${noun} can open Lemma.`;
}

/** Who may create an account, said the way locald says it. Invite-only is the
 *  default because an unset preference means exactly that on the daemon. */
export function joinPolicyCopy(whoCanJoin: WhoCanJoin, mode: SharingMode): string {
    const open = whoCanJoin === "open";
    if (mode === "public") {
        return open
            ? "Anyone with the link can create an account."
            : "Anyone with the link can reach sign-in. Only people you invite can create an account.";
    }
    if (mode === "local_network") {
        return open ? "Anyone on this network can create an account." : "Only people you invite can create an account.";
    }
    return open
        ? "Once shared, anyone who can reach Lemma can create an account."
        : "Once shared, only people you invite can create an account.";
}

/** Whether a transition is under way, from any of the three places that say so. */
export function sharingBusy(sharing: Sharing | null): boolean {
    if (!sharing) return false;
    return sharing.transition_running || !["ready", "error"].includes(sharing.phase || "ready");
}

export type SharingPlan =
    | { kind: "lan"; interface: string }
    | { kind: "public"; provider: TunnelProvider; cloudflareSetup: "automatic" | "existing"; hostname: string; tunnelId: string; tunnelName: string };

/** The enable request for a plan, or the one thing still missing from it. */
export function enablePayload(plan: SharingPlan): { payload: Record<string, unknown> } | { missing: string } {
    if (plan.kind === "lan") {
        if (!plan.interface) return { missing: "Choose the network to share on." };
        return { payload: { mode: "local_network", interface: plan.interface, public_warning_confirmed: false } };
    }
    /* No consent flag from here. The shell sets it only after its own native
       confirmation, and would clear one this page sent. */
    const payload: Record<string, unknown> = { mode: "public", provider: plan.provider };
    if (plan.provider === "cloudflare") {
        const hostname = plan.hostname.trim();
        if (!hostname) return { missing: "Enter the public hostname to create in your Cloudflare zone." };
        payload.cloudflare_setup = plan.cloudflareSetup;
        payload.hostname = hostname;
        if (plan.cloudflareSetup === "existing") {
            if (!plan.tunnelId) return { missing: "Choose one of your named tunnels." };
            payload.cloudflare_tunnel_id = plan.tunnelId;
            payload.cloudflare_tunnel_name = plan.tunnelName;
        }
    }
    return { payload };
}

/** Terminal commands the tunnel's own readiness names, so they can be copied. */
export function setupCommands(readiness: ProviderReadiness | undefined): { command: string | null; text: string }[] {
    return (readiness?.instructions ?? []).map((instruction) => {
        const match = /`([^`]+)`/.exec(instruction);
        return { command: match ? match[1] : null, text: instruction.replace(/`/g, "") };
    });
}

/* ── updates ───────────────────────────────────────────────────────── */

export function formatBytes(bytes: number | null | undefined): string | null {
    if (!bytes || !Number.isFinite(bytes) || bytes <= 0) return null;
    const mb = bytes / (1024 * 1024);
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

/** What an update is about to change, in the words the shell uses too. */
export function postgresMajorChangeMessage(update: AppUpdateStatus): string {
    const from = update.installedPostgresMajor;
    const to = update.candidatePostgresMajor;
    const change = from && to ? `from Postgres ${from} to Postgres ${to}` : "to a different Postgres version";
    return `This update moves Lemma's database ${change}, which Lemma can't migrate automatically yet. `
        + "Nothing was changed: your current version, pods, files and accounts are as they were.";
}

export function updateOffer(update: AppUpdateStatus | null): { blocked: string | null; cost: string } {
    if (!update?.availableVersion) return { blocked: null, cost: "" };
    const runtime = formatBytes(update.runtimeDownloadBytes);
    return {
        blocked: update.dataCompatibility === "postgres-major-change" ? postgresMajorChangeMessage(update) : null,
        /* Honest about what follows the restart: the app is small, the
           runtime it then fetches is not, and someone on a hotspot should
           know before, not after. */
        cost: runtime
            ? `The update is small. After Lemma restarts it downloads about ${runtime} before the workspace opens.`
            : "After Lemma restarts it downloads its runtime once before the workspace opens.",
    };
}

/** The channel is a property of the build, not a switch: a nightly and a
 *  release are different downloads. Said once, rather than offered as a
 *  toggle that would have to install a different app to mean anything. */
export function channelLine(update: AppUpdateStatus | null, channel: string): string {
    const current = update?.channel || channel;
    if (current === "nightly") return "Nightly builds don’t update themselves. Newer ones are on the releases page.";
    if (current === "stable") return "Stable releases. Nightly builds are a separate download from the releases page.";
    return "A development build, which doesn’t update itself.";
}

/* ── advanced: developer credentials ──────────────────────────────── */

export type CredentialForm =
    | "composio" | "google" | "github" | "microsoft" | "deepgram"
    | "slack" | "telegram" | "teams" | "whatsapp" | "resend";

export interface CredentialField {
    key: string;
    label: string;
    /** A secret is written to the vault and never read back — only whether
     *  one is stored. */
    secret?: boolean;
    kind?: "text" | "toggle";
}

export interface CredentialFormSpec {
    form: CredentialForm;
    section: "integrations" | "surfaces";
    title: string;
    /** The one line of consequence. */
    use: string;
    fields: CredentialField[];
    /** Needs a public link for its callbacks to arrive. */
    needsPublicLink?: boolean;
}

/** The forms, in the order people need them. Field keys are the daemon's:
 *  `section.field` for plain values, and the vault name for secrets. */
export const CREDENTIAL_FORMS: CredentialFormSpec[] = [
    { form: "google", section: "integrations", title: "Google", use: "Lets people here connect Gmail, Calendar and Drive.",
        fields: [{ key: "google_client_id", label: "Client ID" }, { key: "integrations.google_client_secret", label: "Client secret", secret: true }] },
    { form: "github", section: "integrations", title: "GitHub", use: "Lets people here connect repositories, issues and pull requests.",
        fields: [{ key: "github_client_id", label: "Client ID" }, { key: "integrations.github_client_secret", label: "Client secret", secret: true }] },
    { form: "microsoft", section: "integrations", title: "Microsoft", use: "Lets people here connect Outlook, OneDrive and other Microsoft accounts.",
        fields: [{ key: "microsoft_client_id", label: "Client ID" }, { key: "integrations.microsoft_client_secret", label: "Client secret", secret: true }] },
    { form: "composio", section: "integrations", title: "Composio", use: "Adds Composio’s connector catalog.",
        fields: [{ key: "composio_enabled", label: "Use Composio connectors", kind: "toggle" },
            { key: "integrations.composio_api_key", label: "API key", secret: true },
            { key: "integrations.composio_webhook_secret", label: "Webhook secret", secret: true }] },
    { form: "deepgram", section: "integrations", title: "Deepgram", use: "Lets teammates speak and listen, and read voice notes.",
        fields: [{ key: "integrations.deepgram_api_key", label: "API key", secret: true }] },
    { form: "slack", section: "surfaces", title: "Slack", use: "The connector app lets each person connect their own Slack; the bot is the one account that answers as Lemma.",
        fields: [{ key: "slack_client_id", label: "Connector client ID" },
            { key: "integrations.slack_client_secret", label: "Connector client secret", secret: true },
            { key: "slack_socket_mode", label: "Answer in Slack without a public link", kind: "toggle" },
            { key: "surfaces.slack_app_token", label: "App token", secret: true },
            { key: "surfaces.slack_bot_token", label: "Bot token", secret: true },
            { key: "surfaces.slack_signing_secret", label: "Signing secret", secret: true }] },
    { form: "telegram", section: "surfaces", title: "Telegram", use: "Lets teammates answer in Telegram, without a public link.",
        fields: [{ key: "telegram_polling", label: "Receive Telegram messages", kind: "toggle" },
            { key: "surfaces.telegram_bot_token", label: "Bot token", secret: true },
            { key: "surfaces.telegram_webhook_secret", label: "Webhook secret", secret: true }] },
    { form: "teams", section: "surfaces", title: "Microsoft Teams", use: "Lets teammates answer in Teams.", needsPublicLink: true,
        fields: [{ key: "teams_app_id", label: "Bot app ID" }, { key: "teams_tenant_id", label: "Tenant ID" },
            { key: "surfaces.teams_app_password", label: "App password", secret: true }] },
    { form: "whatsapp", section: "surfaces", title: "WhatsApp Business", use: "Lets teammates answer on WhatsApp.", needsPublicLink: true,
        fields: [{ key: "whatsapp_phone_number_id", label: "Phone number ID" }, { key: "whatsapp_waba_id", label: "Business account ID" },
            { key: "surfaces.whatsapp_access_token", label: "Access token", secret: true },
            { key: "surfaces.whatsapp_verify_token", label: "Verify token", secret: true },
            { key: "surfaces.whatsapp_app_secret", label: "App secret", secret: true }] },
    { form: "resend", section: "surfaces", title: "Email · Resend", use: "Lets teammates receive and answer email.", needsPublicLink: true,
        fields: [{ key: "resend_inbound_domain", label: "Inbound domain" },
            { key: "surfaces.resend_api_key", label: "API key", secret: true },
            { key: "surfaces.resend_signing_secret", label: "Signing secret", secret: true }] },
];

export function formSpec(form: CredentialForm): CredentialFormSpec {
    return CREDENTIAL_FORMS.find((one) => one.form === form)!;
}

/** Plain values live in one of the two sections, whichever holds the key.
 *  The Slack form spans both: the connector app is an integration, the bot
 *  a surface. */
function sectionHolding(config: ThisMacSnapshot["operator"]["config"], key: string): "integrations" | "surfaces" | null {
    if (key in config.integrations) return "integrations";
    if (key in config.surfaces) return "surfaces";
    return null;
}

/** Whether a form holds anything the owner has set. "Anything", not
 *  "everything": several of these carry independent credentials, and no rule
 *  here could honestly say which are required. */
export function formConfigured(snapshot: ThisMacSnapshot, form: CredentialForm): boolean {
    const config = snapshot.operator.config;
    return formSpec(form).fields.some((field) => {
        if (field.secret) return snapshot.operator.secrets[field.key] === true;
        const section = sectionHolding(config, field.key);
        const value = section ? (config[section] as unknown as Record<string, unknown>)[field.key] : undefined;
        return typeof value === "boolean" ? value : typeof value === "string" && value.trim() !== "";
    });
}

export type Draft = Record<string, string | boolean>;
/** A secret's pending change: left alone, replaced with a value, or removed. */
export type SecretIntent = { action: "keep" } | { action: "replace"; value: string } | { action: "remove" };

export interface SectionPayload {
    expected_revision: number;
    section: { name: "integrations" | "surfaces"; value: IntegrationConfig | SurfaceConfig };
    secrets: Record<string, SecretIntent>;
}

/** The `config.apply` requests one form's draft becomes.
 *
 *  One per section it touches, each carrying that section's whole value —
 *  the daemon replaces a section, it does not merge fields — and only the
 *  secrets that belong to it. A secret typed and then cleared is `keep`, not
 *  `remove`: removing is its own button, because an empty field is how every
 *  secret looks when it is stored. */
export function sectionPayloads(
    snapshot: ThisMacSnapshot,
    form: CredentialForm,
    draft: Draft,
    secrets: Record<string, SecretIntent>,
): SectionPayload[] {
    const config = snapshot.operator.config;
    const values = {
        integrations: { ...config.integrations } as unknown as Record<string, unknown>,
        surfaces: { ...config.surfaces } as unknown as Record<string, unknown>,
    };
    const touched = new Set<"integrations" | "surfaces">();
    const secretsBySection: Record<"integrations" | "surfaces", Record<string, SecretIntent>> = { integrations: {}, surfaces: {} };
    for (const field of formSpec(form).fields) {
        if (field.secret) {
            const section = field.key.split(".")[0] as "integrations" | "surfaces";
            const intent = secrets[field.key];
            if (intent && intent.action !== "keep" && !(intent.action === "replace" && !intent.value.trim())) {
                secretsBySection[section][field.key] = intent.action === "replace" ? { action: "replace", value: intent.value.trim() } : intent;
                touched.add(section);
            }
            continue;
        }
        if (!(field.key in draft)) continue;
        const section = sectionHolding(config, field.key);
        if (!section) continue;
        const next = draft[field.key];
        const value = typeof next === "string" ? next.trim() : next;
        if (values[section][field.key] !== value) {
            values[section][field.key] = value;
            touched.add(section);
        }
    }
    /* Integrations first: the Slack form's connector app is useful on its
       own, and the bot's surface is the one more likely to be refused. */
    return (["integrations", "surfaces"] as const)
        .filter((section) => touched.has(section))
        .map((section) => ({
            expected_revision: config.revision,
            section: { name: section, value: values[section] as unknown as IntegrationConfig & SurfaceConfig },
            secrets: secretsBySection[section],
        }));
}

/* ── point of need ─────────────────────────────────────────────────── */

/** Which developer-credential form a connector's OAuth app lives in on this
 *  install, if any. By id, because these are the catalog's own native
 *  connectors, and the system OAuth client each one needs is the
 *  `CONNECTOR_*_CLIENT_ID` the operator form writes. */
export function oauthFormForConnector(connectorId: string): CredentialForm | null {
    const id = connectorId.toLowerCase();
    if (id === "gmail" || id.startsWith("google")) return "google";
    if (id === "github") return "github";
    if (id.startsWith("microsoft") || id.startsWith("outlook") || id === "onedrive" || id === "sharepoint") return "microsoft";
    if (id === "slack") return "slack";
    return null;
}

/** Which form a channel's bot credentials live in on this install, if any. */
export function credentialFormForChannel(platform: string): CredentialForm | null {
    const key = platform.toUpperCase();
    if (key === "SLACK") return "slack";
    if (key === "TELEGRAM") return "telegram";
    if (key === "TEAMS") return "teams";
    if (key === "WHATSAPP") return "whatsapp";
    if (key === "EMAIL" || key === "RESEND") return "resend";
    return null;
}

/* ── models: what this computer already serves ─────────────────────── */

export interface LocalServer {
    id: "ollama" | "lmstudio";
    name: string;
    baseUrl: string;
}

/** The two local model servers people run, on their default loopback ports.
 *  Lemma never runs a model process of its own: these are endpoints the
 *  person already has, and detecting one only saves them typing its URL. */
export const LOCAL_SERVERS: LocalServer[] = [
    { id: "ollama", name: "Ollama", baseUrl: "http://127.0.0.1:11434/v1" },
    { id: "lmstudio", name: "LM Studio", baseUrl: "http://127.0.0.1:1234/v1" },
];

export interface DetectedServer extends LocalServer {
    models: string[];
}

/** Ask each local server for its models. One that does not answer, or answers
 *  with nothing, is simply not offered: a suggestion that cannot work is not
 *  one.
 *
 *  `api_key: ""` on purpose. Omitted, the shell would attach the operator's
 *  stored provider key to the request — right for re-listing that provider,
 *  and exactly wrong for probing a different endpoint. */
export async function detectLocalServers(
    discover: (payload: Record<string, unknown>) => Promise<unknown> = thisMac.discoverModels,
): Promise<DetectedServer[]> {
    const found = await Promise.all(LOCAL_SERVERS.map(async (server) => {
        try {
            const models = await discover({
                ai: {
                    protocol: "openai_compat",
                    base_url: server.baseUrl,
                    default_model: "",
                    models: [],
                    vision_models: [],
                    allow_private_network: false,
                },
                api_key: "",
            });
            const list = strings(models);
            return list.length ? { ...server, models: list } : null;
        } catch {
            return null;
        }
    }));
    return found.filter((one): one is DetectedServer => one !== null);
}

/** The key a no-auth loopback server is given, matching what locald hands the
 *  backend for the same case (`local_no_auth`). The profile schema wants a
 *  key; the server ignores it. */
export const LOCAL_SERVER_KEY = "lemma-local";

export interface ProviderDraft {
    protocol: "openai" | "anthropic";
    name: string;
    baseUrl: string;
    models: string[];
    /** Null when this provider needs no key (a loopback server). */
    needsKey: boolean;
}

const LOOPBACK = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i;

/** The operator's AI provider on this install, as an organization provider.
 *
 *  Null when none is configured. The key is not here and cannot be: the page
 *  only ever learns that one is stored. So a keyed provider asks for it once
 *  more, and a loopback one needs none. */
export function operatorProvider(snapshot: ThisMacSnapshot): ProviderDraft | null {
    const ai = snapshot.operator.config.ai;
    if (ai.protocol !== "openai_compat" && ai.protocol !== "anthropic_compat") return null;
    if (!ai.base_url || !ai.default_model) return null;
    const models = [ai.default_model, ...ai.models.filter((model) => model !== ai.default_model)];
    const server = LOCAL_SERVERS.find((one) => one.baseUrl === ai.base_url.replace(/\/$/, ""));
    return {
        protocol: ai.protocol === "anthropic_compat" ? "anthropic" : "openai",
        name: server?.name ?? hostName(ai.base_url),
        baseUrl: ai.base_url,
        models,
        needsKey: !LOOPBACK.test(ai.base_url),
    };
}

function hostName(url: string): string {
    try {
        return new URL(url).hostname.replace(/^api\./, "");
    } catch {
        return "AI provider";
    }
}

/** Whether the organization already has a provider on this route, so the
 *  suggestion or the Add to workspace offer is not made twice. */
export function alreadyInWorkspace(baseUrl: string, runtimes: { baseUrl?: string | null; archived?: boolean }[]): boolean {
    const want = baseUrl.replace(/\/+$/, "").toLowerCase();
    return runtimes.some((runtime) => !runtime.archived && (runtime.baseUrl ?? "").replace(/\/+$/, "").toLowerCase() === want);
}

/** Add this computer's provider to the organization.
 *
 *  Deliberately *not* followed by clearing the operator profile. That profile
 *  is the deployment's `system:lemma`, which the backend falls back to
 *  wherever nothing more specific is set: a pod with no default runtime,
 *  conversation titles, summaries, image reading. Clearing it after the move
 *  would quietly take the model away from all of those. So the organization
 *  gains a provider it can pick and manage by name, and the fallback stays
 *  where it was. */
export async function addToWorkspace(
    draft: ProviderDraft,
    apiKey: string,
    add: (key: { protocol: "openai" | "anthropic"; name: string; baseUrl: string; apiKey: string; models: string[] }) => Promise<void>,
): Promise<void> {
    const key = draft.needsKey ? apiKey.trim() : LOCAL_SERVER_KEY;
    if (!key) throw new Error("Enter the API key for " + draft.name + ". Lemma cannot read back the one stored on this computer.");
    await add({ protocol: draft.protocol, name: draft.name, baseUrl: draft.baseUrl, apiKey: key, models: draft.models });
}

/* ── errors ────────────────────────────────────────────────────────── */

/** Daemon and shell errors, said in a way a person can act on. */
export function friendlyError(reason: unknown): string {
    const message = reason instanceof Error ? reason.message : String(reason ?? "");
    if (/not allowed by ACL|only the Lemma workspace on/i.test(message)) {
        return "Lemma can change these settings only from its own window on this computer.";
    }
    if (/control endpoint unavailable|is not connected|disconnected/i.test(message)) {
        return "Lemma’s background service isn’t running. Restarting Lemma usually brings it back.";
    }
    if (/another local operation is running|busy|still finishing/i.test(message)) {
        return "Lemma is already doing something. Wait for it to finish and try again.";
    }
    if (/config-conflict|revision/i.test(message)) {
        return "These settings changed somewhere else. Reopen them to see the current values.";
    }
    return message.replace(/^Error:\s*/, "") || "That didn’t work.";
}
