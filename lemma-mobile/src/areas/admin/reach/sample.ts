import type { AgentSurfaceResponse, SurfaceSetupResponse, SurfaceUpdateRequest } from "lemma-sdk";
import { SAMPLE_LEMMAS } from "../../../data/sample";
import { accountName, readAccount, type ConnectorAccount } from "@web/data/accounts";
import { readPodConnectors, type PodConnector } from "@web/data/pod-connectors";
import type { ReachSource } from "./source";
import type { Answerer, BotGroup, GuidedSetup, MySurface, PlaceOption, SlackAppWorkspace, SlackWorkspace, Surface } from "./types";
import { byEffort, readConnectable, type Connectable } from "@web/data/connectable";
import type { SourceSettings } from "@web/channels/sources";

/** Where Acme's Lemmas are reached, as sample data: shaped like the wire, so
 *  the same readers run, and never a real number, address or workspace.
 *  Numbers are in the 555-01xx range kept for fiction; addresses are on
 *  `.example`, and every page the sample would open is on example.com, so
 *  nothing reaches a real Telegram bot, WhatsApp number or provider. Writes
 *  stay in memory while the app is open.
 *
 *  What it shows: Support answers on Lemma's shared WhatsApp number, its own
 *  email address and a Slack app of its own, with groups on WhatsApp; Launch
 *  on a Telegram bot of its own; Renewals has paused its email and has a
 *  Slack app not finished setting up. Hiring and Books are only in Lemma.
 *  Signing in to a provider is acted out: nothing leaves the phone. */

const SUPPORT = "sample-support-1140";
const LAUNCH = "sample-launch-1440";
const RENEWALS = "sample-renewals-790";
const MIN = 60_000;

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

interface Held {
    podId: string;
    surface: Surface;
    mode: "SYSTEM" | "CUSTOM";
    accountId: string | null;
    agent: string | null;
    routes: { channel_id: string; channel_name: string | null }[];
    allowSend: boolean;
    answersOutsiders: boolean;
    contacts: "off" | "known" | "anyone";
    domains: string[];
    emails: string[];
}

function held(podId: string, surface: Omit<Surface, "active"> & { status: string }, extra: Partial<Held> = {}): Held {
    return {
        podId,
        surface: { ...surface, active: surface.status === "ACTIVE" },
        mode: "SYSTEM",
        accountId: null,
        agent: null,
        routes: [],
        allowSend: false,
        answersOutsiders: true,
        contacts: "off",
        domains: [],
        emails: [],
        ...extra,
    };
}

let PLACES: Held[] = [
    held(SUPPORT, { id: "sf-support-whatsapp", platform: "WHATSAPP", name: "whatsapp", mine: true, agentName: "Support", handle: "+1 555-0142", status: "ACTIVE" }),
    held(SUPPORT, { id: "sf-support-email", platform: "RESEND", name: "email", mine: true, agentName: "Support", handle: "support@inbox.example", email: "support@inbox.example", status: "ACTIVE" },
        { allowSend: true, contacts: "known", domains: ["globex.example"] }),
    held(SUPPORT, { id: "sf-support-slack", platform: "SLACK", name: "slack", mine: true, agentName: "Support", handle: "Support", status: "ACTIVE" },
        { mode: "CUSTOM", accountId: "acc-slack-support", routes: [{ channel_id: "C0HELPDESK", channel_name: "help-desk" }, { channel_id: "C0GLOBEX", channel_name: "customers-globex" }] }),
    /* One of Support's agents with a Telegram bot of its own: it answers as
       that agent, so it is that agent's, not on Support's list. */
    held(SUPPORT, { id: "sf-support-refunds", platform: "TELEGRAM", name: "refunds-telegram", mine: false, agentName: "Refunds", agentKey: "refunds", handle: "@acme_refunds_sample_bot", status: "ACTIVE" },
        { mode: "CUSTOM", accountId: "acc-telegram-refunds", agent: "refunds" }),
    held(LAUNCH, { id: "sf-launch-telegram", platform: "TELEGRAM", name: "telegram", mine: true, agentName: "Launch", handle: "@acme_launch_sample_bot", status: "ACTIVE" },
        { mode: "CUSTOM", accountId: "acc-telegram-launch", allowSend: true, contacts: "anyone" }),
    held(RENEWALS, { id: "sf-renewals-email", platform: "RESEND", name: "email", mine: true, agentName: "Renewals", handle: "renewals@inbox.example", email: "renewals@inbox.example", status: "INACTIVE" }),
    held(RENEWALS, { id: "sf-renewals-slack", platform: "SLACK", name: "slack", mine: true, agentName: "Renewals", handle: "Renewals", status: "NEEDS_SETUP" },
        { mode: "CUSTOM", accountId: "acc-slack-renewals" }),
];

/** The organization's connector accounts, as the wire has them. */
let ACCOUNTS: Record<string, unknown>[] = [
    { id: "acc-slack-support", connector_id: "slack", auth_config_id: "authcfg-slack-support", display_name: "Support in Acme", status: "CONNECTED", install_state: "READY", created_at: "2026-08-14T10:00:00Z" },
    { id: "acc-slack-renewals", connector_id: "slack", auth_config_id: "authcfg-slack-renewals", display_name: "Renewals in Acme", status: "CONNECTED", install_state: "READY", created_at: "2026-09-02T10:00:00Z" },
    { id: "acc-telegram-launch", connector_id: "telegram", auth_config_id: "authcfg-telegram", display_name: "@acme_launch_sample_bot", status: "CONNECTED", install_state: "READY", created_at: "2026-09-20T10:00:00Z" },
    { id: "acc-telegram-refunds", connector_id: "telegram", auth_config_id: "authcfg-telegram", display_name: "@acme_refunds_sample_bot", status: "CONNECTED", install_state: "READY", created_at: "2026-09-22T10:00:00Z" },
    /* A WhatsApp Business number the organization connected and nobody uses:
       offered to every Lemma, and likely another's, since Support is on
       WhatsApp. */
    { id: "acc-whatsapp-care", connector_id: "whatsapp", auth_config_id: "authcfg-whatsapp", display_name: "Acme Care, +1 555-0199", status: "CONNECTED", install_state: "READY", created_at: "2026-07-30T10:00:00Z" },
    /* A Teams account nobody answers on yet: one tap for any Lemma. */
    { id: "acc-teams", connector_id: "teams", auth_config_id: "authcfg-teams", display_name: "Acme on Microsoft 365", status: "CONNECTED", install_state: "READY", is_default: true, created_at: "2026-08-01T10:00:00Z" },
];

const TOKEN_SCHEMA = { type: "object", required: ["bot_token"], properties: { bot_token: { type: "string", title: "Bot token", description: "From @BotFather. It looks like 123456789:AA…", examples: ["123456789:AA…"] } } };
const WHATSAPP_SCHEMA = {
    type: "object",
    required: ["phone_number_id", "access_token"],
    properties: {
        phone_number_id: { type: "string", title: "Phone number ID", description: "On the number's page in Meta's WhatsApp Manager." },
        access_token: { type: "string", title: "Access token", description: "A system user's permanent token." },
    },
};

/** The catalog, as `podSurfaces.available()` answers it for one Lemma. */
function catalogFor(podId: string): unknown[] {
    const systemHolder = (platform: string) => PLACES.find((one) => one.surface.platform === platform && one.mode === "SYSTEM" && one.podId !== podId);
    const claim = (platform: string) => {
        const holder = systemHolder(platform);
        return holder ? { available: false, claimed_by_pod_id: holder.podId, claimed_by_surface_name: holder.surface.name } : { available: true };
    };
    return [
        { platform: "TELEGRAM", connector_id: "telegram", kind: "http", title: "Telegram", connector_available: true, managed_setup_available: true,
            supported_credential_modes: ["CUSTOM", "SYSTEM"], system_claim: claim("TELEGRAM"), connect: { auth_scheme: "API_KEY", credential_schema: TOKEN_SCHEMA } },
        { platform: "WHATSAPP", connector_id: "whatsapp", kind: "http", title: "WhatsApp", connector_available: true,
            supported_credential_modes: ["CUSTOM", "SYSTEM"], system_claim: claim("WHATSAPP"), connect: { auth_scheme: "API_KEY", credential_schema: WHATSAPP_SCHEMA } },
        { platform: "RESEND", connector_id: "resend", kind: "http", title: "Email", connector_available: false, email_domain: "inbox.example",
            supported_credential_modes: ["CUSTOM", "SYSTEM"], system_claim: { available: true } },
        { platform: "SLACK", connector_id: "slack", kind: "http", title: "Slack", connector_available: true,
            supported_credential_modes: ["CUSTOM"], connect: { auth_scheme: "OAUTH2", system_oauth_available: true } },
        { platform: "TEAMS", connector_id: "teams", kind: "http", title: "Microsoft Teams", connector_available: true,
            supported_credential_modes: ["CUSTOM"], connect: { auth_scheme: "OAUTH2", system_oauth_available: false } },
        /* Listed so the phone's way of saying "not here yet" can be seen: no
           Lemma-run identity, and no connector this server has set up. */
        { platform: "DISCORD", connector_id: "discord", kind: "http", title: "Discord", connector_available: false,
            supported_credential_modes: ["CUSTOM"] },
    ];
}

const AGENTS: Record<string, { name: string; kind?: string; label: string }[]> = {
    [SUPPORT]: [{ name: "refunds", label: "Refunds" }, { name: "triage", label: "Triage" }],
    [LAUNCH]: [{ name: "press-kit", label: "Press kit" }],
};

const OPTIONS: PlaceOption[] = [
    { id: "C0HELPDESK", name: "help-desk", isMember: true },
    { id: "C0GLOBEX", name: "customers-globex", isMember: true },
    { id: "C0LAUNCH", name: "launch-room", isMember: false },
];

function group(id: string, channelId: string, platform: string, surfaceName: string, title: string | null, extra: Partial<BotGroup> = {}): BotGroup {
    return {
        id,
        channelId,
        platform,
        title,
        surfaceName,
        answersOutsiders: true,
        welcomesOutsiders: true,
        botAnswersOutsiders: true,
        canManage: true,
        owner: { kind: "MEMBER", id: "user-sam", name: "Sam Okafor" },
        inviteLink: null,
        pending: false,
        sharedExternally: false,
        publicReach: null,
        joined: true,
        ...extra,
    };
}

let GROUPS: (BotGroup & { podId: string })[] = [
    { podId: SUPPORT, ...group("src-globex", "support-globex-rollout", "WHATSAPP", "whatsapp", "Globex rollout", { inviteLink: "https://example.com/whatsapp-invite/globex-rollout" }) },
    { podId: SUPPORT, ...group("src-initech", "support-initech-pilot", "WHATSAPP", "whatsapp", "Initech pilot", { owner: { kind: "MEMBER", id: "me", name: "Ada Lovelace" }, inviteLink: "https://example.com/whatsapp-invite/initech-pilot" }) },
    /* Private, and Ada is not in it: an admin sees it, to join. */
    { podId: SUPPORT, ...group("src-escalations", "support-escalations", "WHATSAPP", "whatsapp", "Escalations", { joined: false, owner: null, answersOutsiders: false, welcomesOutsiders: false }) },
    { podId: SUPPORT, ...group("src-slack-globex", "support-slack-globex", "SLACK", "slack", "customers-globex", { sharedExternally: true }) },
    { podId: LAUNCH, ...group("src-beta", "launch-week", "TELEGRAM", "telegram", "Launch beta testers", { owner: { kind: "MEMBER", id: "user-dev", name: "Dev Mehta" } }) },
];

let accountSerial = 0;
let pendingConnector: { connectorId: string; authConfigId: string } | null = null;
let guidedStarted = 0;
let guidedPod = "";
let telegramLinkAt = 0;
let telegramLinkPod = "";
let managed: SlackWorkspace[] = [];
let appWorkspaces: SlackAppWorkspace[] = [];

function lemmaName(podId: string): string {
    return SAMPLE_LEMMAS.find((one) => one.id === podId)?.name ?? "Lemma";
}

function slug(podId: string): string {
    return lemmaName(podId).toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function find(podId: string, name: string): Held {
    const found = PLACES.find((one) => one.podId === podId && one.surface.name === name);
    if (!found) throw Object.assign(new Error("That place is not connected any more."), { statusCode: 404 });
    return found;
}

function accounts(): ConnectorAccount[] {
    return ACCOUNTS.map(readAccount).filter((one): one is ConnectorAccount => one !== null);
}

/** In sample data each usable account is shared into every Lemma as a
 *  connector named for its app: a place can answer through any of them. */
function sharedConnectors(): PodConnector[] {
    return readPodConnectors(accounts().filter((one) => one.usable).map((one) => ({
        slug: one.connectorId + "-" + one.id.slice(-4), connector_id: one.connectorId, mode: "SHARED", status: "READY",
        shared_pod_account_id: one.id, shared_account: { display_name: accountName(one, one.connectorId) },
    })));
}

/** One account answers in one place across the organization. */
function bindAccount(podId: string, platform: string, accountId: string): Surface {
    const holder = PLACES.find((one) => one.accountId === accountId);
    if (holder) {
        throw Object.assign(new Error(`AGENT_SURFACE_CREDENTIAL_CONFLICT: that account is already used by another surface (${lemmaName(holder.podId)}).`), { statusCode: 409 });
    }
    return made(podId, platform, "CUSTOM", accountId);
}

function made(podId: string, platform: string, mode: "SYSTEM" | "CUSTOM", accountId: string | null): Surface {
    const name = platform.toLowerCase() === "resend" ? "email" : platform.toLowerCase();
    if (PLACES.some((one) => one.podId === podId && one.surface.name === name)) {
        throw Object.assign(new Error(`It already answers on ${platform === "RESEND" ? "email" : platform.charAt(0) + platform.slice(1).toLowerCase()}.`), { statusCode: 409 });
    }
    const handle = platform === "RESEND" ? `${slug(podId)}@inbox.example`
        : platform === "WHATSAPP" ? (mode === "SYSTEM" ? "+1 555-0142" : "+1 555-0199")
        : platform === "TELEGRAM" ? (mode === "SYSTEM" ? "@lemma_shared_sample_bot" : `@acme_${slug(podId)}_sample_bot`)
        : lemmaName(podId);
    const status = platform === "TEAMS" ? "PENDING_ADMIN_CONSENT" : "ACTIVE";
    const surface: Surface = {
        id: `sf-${slug(podId)}-${name}`,
        platform,
        name,
        mine: true,
        agentName: lemmaName(podId),
        handle,
        email: platform === "RESEND" ? handle : undefined,
        active: status === "ACTIVE",
        status,
    };
    PLACES = [...PLACES, held(podId, { ...surface, status }, { mode, accountId })];
    return surface;
}

function setupOf(place: Held): SurfaceSetupResponse {
    const status = place.surface.status ?? "ACTIVE";
    const actions = status === "NEEDS_SETUP" ? [
        {
            key: "install",
            title: "Install the app in your workspace",
            description: `Slack made ${place.surface.handle}, and it has not been installed yet.`,
            steps: ["Open the app's settings in Slack.", "Choose Install to Workspace, then Allow."],
            link: "https://api.slack.com/apps",
            link_label: "Open Slack apps",
        },
        {
            key: "events",
            title: "Point its events at Lemma",
            description: "Under Event Subscriptions, paste this address, and check the signing secret matches.",
            fields: [
                { label: "Request address", value: "https://api.example/surfaces/slack/events" },
                { label: "Signing secret", value: "sample-signing-secret-not-real", secret: true },
            ],
        },
    ] : [];
    return {
        exists: true,
        ready: status === "ACTIVE" || status === "INACTIVE",
        status,
        platform: place.surface.platform,
        actions,
        admin_consent: status === "PENDING_ADMIN_CONSENT" ? { required: true, granted: false, consent_url: "https://example.com/admin-consent?sample=1" } : null,
        guide: { platform: place.surface.platform, title: "", summary: "", docs_path: "" },
    } as unknown as SurfaceSetupResponse;
}

function detailOf(place: Held): AgentSurfaceResponse {
    const surface = place.surface;
    return {
        id: surface.id,
        name: surface.name,
        platform: surface.platform,
        pod_id: place.podId,
        status: surface.status ?? "ACTIVE",
        agent_name: place.agent,
        uses_default_agent: !place.agent,
        credential_mode: place.mode,
        account_id: place.accountId,
        reach: { handle: surface.handle, email: surface.email ?? null },
        config: {
            channels: place.routes,
            send_policy: { allow_send: place.allowSend },
            groups: { answers_outsiders: place.answersOutsiders },
            contacts: { answer: place.contacts },
            identity: { allowed_domains: place.domains, allowed_email_addresses: place.emails },
        },
    } as unknown as AgentSurfaceResponse;
}

function apply(place: Held, patch: SurfaceUpdateRequest): Held {
    const next: Held = { ...place, surface: { ...place.surface } };
    if (typeof patch.is_enabled === "boolean") {
        next.surface.status = patch.is_enabled ? (place.surface.status === "INACTIVE" ? "ACTIVE" : place.surface.status) : "INACTIVE";
        next.surface.active = next.surface.status === "ACTIVE";
    }
    if (patch.default_agent_name !== undefined) next.agent = patch.default_agent_name ?? null;
    const config = patch.config;
    if (config?.channels) next.routes = config.channels.map((route) => ({ channel_id: String(route.channel_id ?? ""), channel_name: route.channel_name ?? null }));
    if (config?.send_policy) next.allowSend = Boolean(config.send_policy.allow_send);
    if (config?.groups && typeof config.groups.answers_outsiders === "boolean") next.answersOutsiders = config.groups.answers_outsiders;
    if (config?.contacts?.answer) next.contacts = config.contacts.answer as Held["contacts"];
    if (config?.identity) {
        next.domains = config.identity.allowed_domains ?? [];
        next.emails = config.identity.allowed_email_addresses ?? [];
    }
    return next;
}

export function reachSample(): ReachSource {
    return {
        async places(podId) {
            await wait(120);
            return PLACES.filter((one) => one.podId === podId).map((one) => ({ ...one.surface }));
        },
        async catalog(podId) {
            await wait(160);
            return catalogFor(podId).map(readConnectable).filter((entry): entry is Connectable => entry !== null).sort(byEffort);
        },
        async accounts() {
            await wait(100);
            return accounts();
        },
        async mine() {
            await wait(80);
            return PLACES.map((one): MySurface => ({ platform: one.surface.platform, podId: one.podId, name: one.surface.name }));
        },
        async connectSystem(podId, platform) {
            await wait(900);
            if (platform === "WHATSAPP" && PLACES.some((one) => one.surface.platform === "WHATSAPP" && one.mode === "SYSTEM")) {
                throw new Error("Lemma's shared number is already taken elsewhere in this organization.");
            }
            return made(podId, platform, "SYSTEM", null);
        },
        async podConnectors() {
            await wait(100);
            return sharedConnectors();
        },
        async connectPodConnector(podId, platform, podConnector) {
            await wait(700);
            const accountId = sharedConnectors().find((one) => one.slug === podConnector)?.sharedPodAccountId ?? null;
            if (!accountId) throw Object.assign(new Error(podConnector + " is not one of this Lemma's connectors."), { statusCode: 404 });
            return bindAccount(podId, platform, accountId);
        },
        async connectOwnAccount(podId, platform, accountId) {
            await wait(700);
            return bindAccount(podId, platform, accountId);
        },
        async disconnect(podId, name) {
            await wait(500);
            PLACES = PLACES.filter((one) => !(one.podId === podId && one.surface.name === name));
            GROUPS = GROUPS.filter((one) => !(one.podId === podId && one.surfaceName === name));
        },

        async detail(podId, name) {
            await wait(140);
            return detailOf(find(podId, name));
        },
        async setup(podId, name) {
            await wait(120);
            return setupOf(find(podId, name));
        },
        async options() {
            await wait(160);
            return OPTIONS.map((one) => ({ ...one }));
        },
        async answerers(podId) {
            await wait(90);
            const front: Answerer = { name: "pod_default", label: lemmaName(podId), front: true };
            return [front, ...(AGENTS[podId] ?? []).map((agent) => ({ name: agent.name, label: agent.label, front: false }))];
        },
        async update(podId, name, patch) {
            await wait(450);
            PLACES = PLACES.map((one) => (one.podId === podId && one.surface.name === name ? apply(one, patch) : one));
        },

        async groups(podId, name) {
            await wait(140);
            /* A Telegram group arrives a few seconds after its link is opened. */
            if (telegramLinkAt && telegramLinkPod === podId && Date.now() - telegramLinkAt > 6000) {
                telegramLinkAt = 0;
                const id = `src-tg-${GROUPS.length}`;
                GROUPS = [...GROUPS, { podId, ...group(id, `${slug(podId)}-partners`, "TELEGRAM", name, "Partners chat", { owner: { kind: "MEMBER", id: "me", name: "Ada Lovelace" } }) }];
            }
            return GROUPS.filter((one) => one.podId === podId && one.surfaceName === name).map(({ podId: _pod, ...rest }) => rest);
        },
        async joinGroup(podId, channelId) {
            await wait(400);
            GROUPS = GROUPS.map((one) => (one.podId === podId && one.channelId === channelId ? { ...one, joined: true } : one));
        },
        async group(podId, channelId, sourceId) {
            await wait(200);
            const found = GROUPS.find((one) => one.podId === podId && one.channelId === channelId && one.id === sourceId);
            if (!found) throw Object.assign(new Error("That group is not here any more."), { statusCode: 404 });
            /* WhatsApp confirms the group, and its link, a few seconds on. */
            if (found.pending && Date.now() - Number(found.id.split("-").pop()) > 3500) {
                const ready = { ...found, pending: false, inviteLink: "https://example.com/whatsapp-invite/new-group" };
                GROUPS = GROUPS.map((one) => (one.id === found.id ? ready : one));
                const { podId: _pod, joined: _joined, ...settings } = ready;
                return settings;
            }
            const { podId: _pod, joined: _joined, ...settings } = found;
            return settings;
        },
        async startWhatsAppGroup(podId, body) {
            await wait(800);
            const started = Date.now();
            const id = `src-wa-${started}`;
            const fresh = { podId, ...group(id, `${slug(podId)}-group-${started}`, "WHATSAPP", body.surfaceName, body.title.trim(), {
                pending: true,
                answersOutsiders: body.answersOutsiders,
                welcomesOutsiders: body.answersOutsiders,
                owner: { kind: "MEMBER" as const, id: "me", name: "Ada Lovelace" },
            }) };
            GROUPS = [...GROUPS, fresh];
            const { podId: _pod, joined: _joined, ...settings } = fresh;
            return settings as SourceSettings;
        },
        async telegramLink(podId) {
            await wait(400);
            telegramLinkAt = Date.now();
            telegramLinkPod = podId;
            return { url: `https://example.com/telegram/add-to-group?lemma=${slug(podId)}`, expiresAt: new Date(Date.now() + 60 * MIN).toISOString() };
        },

        async startAccount(_orgId, connectorId, options = {}) {
            await wait(500);
            const before = accounts().filter((one) => one.connectorId === connectorId && one.usable).map((one) => one.id);
            const authConfigId = options.authConfigId ?? `authcfg-${connectorId}`;
            pendingConnector = { connectorId, authConfigId };
            return { authorizeUrl: `https://example.com/consent/${connectorId}?sample=1`, before, authConfigId };
        },
        /* Nothing opens: the provider says yes after a moment, as if the
           person had signed in and come back. */
        async signIn() {
            await wait(1400);
            if (!pendingConnector) return null;
            accountSerial += 1;
            const id = `acc-${pendingConnector.connectorId}-new-${accountSerial}`;
            ACCOUNTS = [...ACCOUNTS, {
                id,
                connector_id: pendingConnector.connectorId,
                auth_config_id: pendingConnector.authConfigId,
                display_name: pendingConnector.connectorId === "slack" ? "Acme workspace" : pendingConnector.connectorId === "teams" ? "Acme on Microsoft 365" : "Acme",
                status: "CONNECTED",
                install_state: "READY",
                created_at: new Date().toISOString(),
            }];
            const connector = pendingConnector.connectorId;
            pendingConnector = null;
            return { connect: "connected", connector, account: id, reason: null };
        },
        async findAccount(_orgId, connectorId, before, authConfigId) {
            await wait(250);
            const seen = new Set(before);
            return accounts().find((one) => one.connectorId === connectorId && one.usable && !seen.has(one.id) && (!authConfigId || one.authConfigId === authConfigId))?.id ?? "";
        },
        async createAccount(_orgId, entry, credentials) {
            await wait(700);
            const token = String(credentials.bot_token ?? credentials.access_token ?? "").trim();
            if (entry.platform === "TELEGRAM" && !/^\d{5,}:[A-Za-z0-9_-]{20,}$/.test(token)) {
                throw new Error("Telegram did not accept that token. Copy it again from @BotFather, all of it.");
            }
            accountSerial += 1;
            const id = `acc-${entry.connectorId}-typed-${accountSerial}`;
            ACCOUNTS = [...ACCOUNTS, { id, connector_id: entry.connectorId, auth_config_id: `authcfg-${entry.connectorId}`, display_name: entry.title, status: "CONNECTED", install_state: "READY", created_at: new Date().toISOString() }];
            return id;
        },
        async removeAccount(_orgId, accountId) {
            await wait(200);
            ACCOUNTS = ACCOUNTS.filter((one) => one.id !== accountId);
        },

        async startGuided(podId) {
            await wait(600);
            guidedStarted = Date.now();
            guidedPod = podId;
            return guided("PENDING");
        },
        async checkGuided(podId) {
            await wait(250);
            /* Ready after a few seconds, so the waiting can be seen. */
            if (guidedPod === podId && Date.now() - guidedStarted > 7000) {
                if (!PLACES.some((one) => one.podId === podId && one.surface.platform === "TELEGRAM" && one.surface.mine)) {
                    accountSerial += 1;
                    const accountId = `acc-telegram-guided-${accountSerial}`;
                    ACCOUNTS = [...ACCOUNTS, { id: accountId, connector_id: "telegram", auth_config_id: "authcfg-telegram", display_name: `@acme_${slug(podId)}_sample_bot`, status: "CONNECTED", install_state: "READY", created_at: new Date().toISOString() }];
                    made(podId, "TELEGRAM", "CUSTOM", accountId);
                }
                return guided("READY", `@acme_${slug(podId)}_sample_bot`);
            }
            return guided("PENDING");
        },

        async slackManaged() {
            await wait(300);
            return managed.slice();
        },
        async bindSlackManager() {
            await wait(500);
            const bound = { teamId: "T0ACME", teamName: "Acme" };
            managed = [bound];
            return bound;
        },
        async slackAppWorkspaces() {
            await wait(250);
            return { canManage: true, workspaces: appWorkspaces.slice() };
        },
        async saveSlackToken(_podId, refreshToken) {
            await wait(600);
            if (!refreshToken.trim().startsWith("xoxe-")) {
                throw new Error("Slack did not accept that refresh token. Generate a new configuration token and paste its refresh token, the one that starts xoxe-.");
            }
            const saved = { teamId: "T0ACME", teamName: "Acme", needsNewToken: false };
            appWorkspaces = [saved];
            return saved;
        },
        async createSlackApp(podId) {
            await wait(900);
            return { appId: `A0${slug(podId).toUpperCase()}`, authConfigId: `authcfg-slack-${slug(podId)}-${Date.now()}` };
        },
        async slackManifest(name) {
            await wait(250);
            return {
                display_information: { name, description: `Answers as ${name} in Slack.` },
                features: { bot_user: { display_name: name, always_online: true } },
                oauth_config: {
                    scopes: { bot: ["app_mentions:read", "chat:write", "im:history", "im:read", "im:write"] },
                    redirect_urls: ["https://api.example/connectors/oauth/callback"],
                },
                settings: { event_subscriptions: { request_url: "https://api.example/surfaces/slack/events", bot_events: ["app_mention", "message.im"] } },
            };
        },
        async addOwnApp(_orgId, connectorId) {
            await wait(600);
            accountSerial += 1;
            return `authcfg-${connectorId}-own-${accountSerial}`;
        },
    };
}

function guided(status: string, bot?: string): GuidedSetup {
    return {
        setupId: "sample-setup",
        launchUrl: "https://example.com/telegram/setup?sample=1",
        managerBot: "Lemma's setup bot",
        status,
        botUsername: bot,
        botLaunchUrl: undefined,
        expiresAt: new Date(guidedStarted + 15 * MIN).toISOString(),
    };
}
