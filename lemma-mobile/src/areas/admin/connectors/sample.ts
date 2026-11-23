import { readAccount, readConnector, type Connector, type ConnectorAccount } from "@web/data/accounts";
import { canInstallWithDefaults, freshInstallName, heldInstall, isBringYourOwn, kindNamed, primaryKind, type CatalogEntry, type Install } from "@web/connect/install";
import { REDACTED } from "@web/connect/schema";
import { readSignIn, snapshot } from "./sign-in";
import type { ConnectorsSource, InstallStep, InstallationChoice, PendingSignIn, SignInResult } from "./source";

/** Sample data: Acme's connectors, shaped like the wire so the sample runs
 *  the real readers (`readConnector`, `readAccount`) and the real rules
 *  (`web/install.ts`). Never a real organization or credential: every
 *  address is on `acme.example` or `example.invalid`, and every secret
 *  reads back masked, as the server's do.
 *
 *  Each state the screens have to say is here on purpose: an account with
 *  no name (Slack, as it arrives), one signed out (Notion), a GitHub App
 *  authorised and not installed, one that reaches two installations, two
 *  installs of Gmail (Lemma's app and Acme's own), an MCP server that signs
 *  in through a browser and nobody has, an install left with nobody on it
 *  (Linear), and a toolkit that needs the organization's own app first
 *  (HubSpot).
 *
 *  Writes stay in memory while the app is open. Nothing opens a browser:
 *  the sample stands in for the provider. A credential containing "wrong"
 *  is refused, an address on `localhost` is refused as private, and a server
 *  address containing "down" refuses to list its operations, so every
 *  failure can be tried too. */

const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));

const object = (properties: Record<string, unknown>, required: string[] = []) => ({ type: "object", properties, required, additionalProperties: false });
const secret = (title: string, description?: string) => ({ type: "string", title, format: "password", ...(description ? { description } : {}) });
const text = (title: string, description?: string, extra: Record<string, unknown> = {}) => ({ type: "string", title, ...(description ? { description } : {}), ...extra });

/** An organization's own OAuth app, as a native OAuth connector describes it. */
const OWN_APP = object({
    client_id: text("Client ID"),
    client_secret: secret("Client secret"),
}, ["client_id", "client_secret"]);
const OAUTH_DEFAULTS = { authorization_url: "https://provider.example.invalid/oauth/authorize", token_url: "https://provider.example.invalid/oauth/token" };

const nativeOAuth = (ownApp: boolean) => ({
    kind: "http", auth_scheme: "OAUTH2", system_default_available: true,
    ...(ownApp ? { supports_org_custom_oauth: true, oauth2_defaults: OAUTH_DEFAULTS, config_schema: OWN_APP } : { config_schema: object({}) }),
});
const composio = (scheme: "OAUTH2" | "API_KEY", extra: Record<string, unknown> = {}) => ({
    kind: "composio", auth_scheme: scheme, system_default_available: true, discovery: "none", ...extra,
});

/* The catalog, as `connectors.list` sends it (detail adds the redirect URI). */
const CATALOG: Record<string, unknown>[] = [
    { id: "slack", title: "Slack", description: "Messages and threads in your workspace.", kinds: [nativeOAuth(true)] },
    { id: "gmail", title: "Gmail", description: "Read and send as a mailbox.", kinds: [nativeOAuth(true)] },
    { id: "github", title: "GitHub", description: "Repositories, issues and pull requests.", kinds: [nativeOAuth(false)] },
    { id: "jira", title: "Jira", description: "Issues, boards and sprints.", kinds: [nativeOAuth(true)] },
    { id: "microsoft_teams", title: "Microsoft Teams", description: "Chats and meetings in Microsoft 365.", kinds: [nativeOAuth(true)] },
    { id: "notion", title: "Notion", description: "Pages and databases.", kinds: [composio("OAUTH2")] },
    { id: "linear", title: "Linear", description: "Issues, projects and cycles.", kinds: [composio("OAUTH2")] },
    { id: "googlecalendar", title: "Google Calendar", description: "Events, invitations and free time.", kinds: [composio("OAUTH2")] },
    { id: "googledrive", title: "Google Drive", description: "Files and folders.", kinds: [composio("OAUTH2")] },
    { id: "asana", title: "Asana", description: "Tasks and projects.", kinds: [composio("OAUTH2")] },
    { id: "airtable", title: "Airtable", description: "Bases, tables and records.", kinds: [composio("OAUTH2")] },
    { id: "zendesk", title: "Zendesk", description: "Tickets and help-centre articles.", kinds: [composio("OAUTH2")] },
    { id: "calendly", title: "Calendly", description: "Booking links and scheduled events.", kinds: [composio("OAUTH2")] },
    {
        id: "shopify", title: "Shopify", description: "Orders, products and customers.",
        /* A sign-in that needs a question answered first: which store. */
        kinds: [composio("OAUTH2", { config_schema: object({ subdomain: text("Store name", "The part before .myshopify.com.", { examples: ["acme-goods"] }) }, ["subdomain"]) })],
    },
    {
        id: "hubspot", title: "HubSpot", description: "Contacts, companies and deals.",
        /* Composio holds no credentials for it: the organization's own app comes first. */
        kinds: [composio("OAUTH2", {
            system_default_available: false,
            install_config_schema: object({
                client_id: text("Client ID"),
                client_secret: secret("Client secret"),
                oauth_redirect_uri: text("Redirect URL", "Register this with your app.", { default: "https://auth.example.invalid/api/v1/auth/callback" }),
            }, ["client_id", "client_secret"]),
        })],
    },
    {
        id: "stripe", title: "Stripe", description: "Payments, customers and invoices.",
        kinds: [composio("API_KEY", { config_schema: object({ api_key: secret("Secret key", "A restricted key is enough.") }, ["api_key"]) })],
    },
    {
        id: "telegram", title: "Telegram", description: "Messages people send to your Telegram account.",
        kinds: [{ kind: "http", auth_scheme: "API_KEY", system_default_available: true, config_schema: object({}),
            credential_schema: object({ bot_token: secret("Bot token", "From BotFather, in Telegram.") }, ["bot_token"]) }],
    },
    {
        id: "whatsapp", title: "WhatsApp Business", description: "The number your customers already message.",
        kinds: [{ kind: "http", auth_scheme: "API_KEY", system_default_available: true, config_schema: object({}),
            credential_schema: object({
                access_token: secret("Access token"),
                phone_number_id: text("Phone number ID"),
                waba_id: text("WhatsApp Business account ID"),
            }, ["access_token", "phone_number_id"]) }],
    },
    {
        id: "mcp", title: "MCP Server", description: "Tools from any server speaking MCP.",
        kinds: [{ kind: "mcp", auth_scheme: "API_KEY", system_default_available: false, discovery: "mcp",
            config_schema: object({
                server_url: text("Server URL", "The server's streamable-HTTP endpoint, such as https://mcp.example.com/mcp. Private, loopback and link-local addresses are refused."),
                extra_headers: { type: "object", title: "Extra headers", description: "Sent with every request to the server.", additionalProperties: { type: "string" } },
            }, ["server_url"]),
            credential_schema: object({ bearer_token: secret("Bearer token", "Optional. Sent as an Authorization header. Leave empty if the server needs no auth.") }) }],
    },
    {
        id: "openapi", title: "OpenAPI (custom API)", description: "Any API with an OpenAPI description.",
        kinds: [{ kind: "http", auth_scheme: "API_KEY", system_default_available: false, discovery: "openapi",
            config_schema: object({
                spec_url: text("Spec URL", "URL of the API's OpenAPI JSON document, such as https://api.example.com/openapi.json."),
                server_url: text("Base URL", "Optional. Where requests actually go."),
                default_headers: { type: "object", title: "Default headers", description: "Sent with every request.", additionalProperties: { type: "string" } },
            }, ["spec_url"]),
            credential_schema: object({
                access_token: secret("Bearer token", "Optional. Sent as an Authorization header."),
                api_key: secret("API key", "Optional. Sent the way the spec's security scheme says to."),
            }) }],
    },
    {
        id: "sql", title: "SQL Database", description: "A PostgreSQL database, read-only.",
        kinds: [{ kind: "sql", auth_scheme: "API_KEY", system_default_available: false, discovery: "none",
            config_schema: object({
                dialect: { type: "string", title: "Dialect", description: "PostgreSQL is the only engine supported today.", enum: ["postgresql"], default: "postgresql" },
                host: text("Host", "Hostname of the database server. It has to be reachable from Lemma."),
                port: { type: "integer", title: "Port", default: 5432 },
                database: text("Database", "Which database on that server to query."),
            }, ["dialect", "host", "database"]),
            credential_schema: object({
                username: text("Username", "A read-only role is enough."),
                password: secret("Password", "Stored encrypted, and never shown again after you save."),
            }, ["username", "password"]) }],
    },
];

const REDIRECT_URI = "https://api.example.invalid/connectors/oauth/callback";

const DAY = 24 * 60 * 60_000;
const ago = (days: number) => new Date(Date.now() - days * DAY).toISOString();

const install = (id: string, connector: string, kind: string, name: string, extra: Partial<Install> = {}): Install => ({
    id, connector_id: connector, kind, name, status: "ACTIVE", is_default: true, config_source: "SYSTEM_DEFAULT",
    auth_scheme: kind === "composio" || kind === "http" ? "OAUTH2" : "API_KEY", config: null, ...extra,
});

let INSTALLS: Install[] = [
    install("ac-slack", "slack", "http", "slack"),
    install("ac-gmail", "gmail", "http", "gmail"),
    install("ac-gmail-own", "gmail", "http", "gmail-2", {
        is_default: false, config_source: "ORG_CUSTOM",
        config: { client_id: "acme-sample-client.apps.example.invalid", client_secret: REDACTED },
    }),
    install("ac-github", "github", "http", "github"),
    install("ac-notion", "notion", "composio", "notion"),
    /* Enabled, and nobody has connected: a sign-in abandoned half-way. */
    install("ac-linear", "linear", "composio", "linear"),
    install("ac-telegram", "telegram", "http", "telegram", { auth_scheme: "API_KEY" }),
    install("ac-stripe", "stripe", "composio", "stripe", { auth_scheme: "API_KEY" }),
    install("ac-mcp-docs", "mcp", "mcp", "Docs search", {
        config_source: "ORG_CUSTOM",
        config: { server_url: "https://mcp.acme.example/docs/mcp", extra_headers: { "X-Team": REDACTED } },
    }),
    /* Its server answered with an authorization server: it signs in through a browser, and nobody has. */
    install("ac-mcp-support", "mcp", "mcp", "Support tools", {
        is_default: false, config_source: "ORG_CUSTOM", auth_scheme: "OAUTH2",
        config: { server_url: "https://tools.acme.example/oauth/mcp" },
    }),
    install("ac-sql", "sql", "sql", "Orders (staging)", {
        config_source: "ORG_CUSTOM",
        config: { dialect: "postgresql", host: "db.acme.example", port: 5432, database: "orders" },
    }),
];

/* Ada's accounts, as `connectors.accounts.list` sends them. */
let ACCOUNTS: Record<string, unknown>[] = [
    /* No display name, exactly as Slack returns one. */
    { id: "acc-slack", connector_id: "slack", auth_config_id: "ac-slack", provider_account_id: "U0SAMPLE01", status: "CONNECTED", install_state: "READY", created_at: ago(70) },
    { id: "acc-gmail", connector_id: "gmail", auth_config_id: "ac-gmail", display_name: "ada@acme.example", status: "CONNECTED", install_state: "READY", is_default: true, created_at: ago(84) },
    { id: "acc-gmail-ops", connector_id: "gmail", auth_config_id: "ac-gmail-own", display_name: "ops@acme.example", status: "CONNECTED", install_state: "READY", created_at: ago(12) },
    { id: "acc-github", connector_id: "github", auth_config_id: "ac-github", display_name: "acme-eng", status: "CONNECTED", install_state: "INSTALL_REQUIRED", created_at: ago(4) },
    { id: "acc-github-2", connector_id: "github", auth_config_id: "ac-github", display_name: "ada-l", status: "CONNECTED", install_state: "CHOOSE_INSTALL", created_at: ago(2) },
    { id: "acc-notion", connector_id: "notion", auth_config_id: "ac-notion", display_name: "Acme workspace", status: "REAUTH_REQUIRED", install_state: "READY", created_at: ago(113) },
    { id: "acc-telegram", connector_id: "telegram", auth_config_id: "ac-telegram", display_name: "Acme help desk", status: "CONNECTED", install_state: "READY", created_at: ago(30) },
    { id: "acc-stripe", connector_id: "stripe", auth_config_id: "ac-stripe", display_name: "Acme (test mode)", status: "CONNECTED", install_state: "READY", created_at: ago(51) },
    { id: "acc-mcp-docs", connector_id: "mcp", auth_config_id: "ac-mcp-docs", status: "CONNECTED", install_state: "READY", created_at: ago(9) },
    { id: "acc-sql", connector_id: "sql", auth_config_id: "ac-sql", display_name: "readonly", status: "CONNECTED", install_state: "READY", created_at: ago(20) },
];

/* The installations each GitHub account reaches. */
const CHOICES: Record<string, InstallationChoice[]> = {
    "acc-github-2": [
        { installationId: "inst-acme", login: "acme-eng", type: "Organization", repositories: "selected" },
        { installationId: "inst-ada", login: "ada-l", type: "User", repositories: "all" },
    ],
};

let made = 0;
const nextId = (prefix: string) => `${prefix}-sample-${++made}`;

const entryOf = (connectorId: string): CatalogEntry => {
    const raw = CATALOG.find((one) => one.id === connectorId);
    if (!raw) throw new Error("There is no such connector.");
    return raw as unknown as CatalogEntry;
};

const accounts = () => ACCOUNTS.map(readAccount).filter((one): one is ConnectorAccount => one !== null);

/** The account rows as Ada's are kept: masked, and shaped as the wire. */
function patchAccount(id: string, patch: Record<string, unknown>): void {
    ACCOUNTS = ACCOUNTS.map((raw) => (raw.id === id ? { ...raw, ...patch } : raw));
}

/** A sample credential that is refused: any value saying "wrong". */
function refuses(values: Record<string, unknown>): boolean {
    return Object.values(values).some((value) => typeof value === "string" && /wrong/i.test(value));
}

/** An address the server's guard would refuse: private or loopback. */
function privateAddress(values: Record<string, unknown> | undefined): boolean {
    return Object.entries(values ?? {}).some(([key, value]) =>
        (key.endsWith("_url") || key === "host") && typeof value === "string" && /localhost|127\.0\.0\.1|\b10\.\d|192\.168\./i.test(value));
}

/** The settings as the server reads them back: every secret masked. */
function masked(config: Record<string, unknown> | undefined, entry: CatalogEntry, kind: string | undefined): Record<string, unknown> | null {
    if (!config) return null;
    const schema = (kindNamed(entry, kind ?? null) ?? primaryKind(entry))?.config_schema as { properties?: Record<string, { format?: string }> } | undefined;
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(config)) {
        if (schema?.properties?.[key]?.format === "password" || /secret|token|password/i.test(key)) out[key] = REDACTED;
        else if (value && typeof value === "object" && !Array.isArray(value)) out[key] = Object.fromEntries(Object.keys(value).map((name) => [name, REDACTED]));
        else out[key] = value;
    }
    return out;
}

/** Merge an edit into what is held: a mask sent back keeps the value. */
function merged(held: Record<string, unknown> | null | undefined, sent: Record<string, unknown>): Record<string, unknown> {
    const out: Record<string, unknown> = { ...(held ?? {}) };
    for (const [key, value] of Object.entries(sent)) {
        if (value === REDACTED) continue;
        out[key] = value;
    }
    return out;
}

const where = (config: Record<string, unknown> | null | undefined) => String(config?.server_url ?? config?.spec_url ?? config?.host ?? "");

export function connectorsSample(): ConnectorsSource {
    return {
        async catalog() {
            await wait(160);
            return CATALOG.map(readConnector).filter((one): one is Connector => one !== null).sort((a, b) => a.title.localeCompare(b.title));
        },

        async detail(connectorId) {
            await wait(120);
            return { entry: entryOf(connectorId), redirectUri: REDIRECT_URI };
        },

        async accounts() {
            await wait(110);
            return accounts();
        },

        async installs() {
            await wait(100);
            return INSTALLS.map((one) => ({ ...one }));
        },

        async install(_orgId, wanted) {
            await wait(140);
            const held = INSTALLS.find((one) => one.id === wanted.id);
            if (!held) throw new Error("That install is no longer here.");
            return { ...held };
        },

        async signIn(_orgId, { connectorId, installId, fields }): Promise<SignInResult> {
            const before = snapshot(accounts().filter((one) => one.connectorId === connectorId));
            let installHere = installId ? INSTALLS.find((one) => one.id === installId) ?? null : heldInstall(INSTALLS, connectorId);
            if (!installHere) {
                const entry = entryOf(connectorId);
                const kind = primaryKind(entry);
                if (!canInstallWithDefaults(kind)) throw new Error(`${entry.title} has no sign-in app here yet. One has to be added under Connectors before anybody can connect it.`);
                installHere = install(nextId("ac"), connectorId, kind?.kind ?? "http", freshInstallName(connectorId, INSTALLS.map((one) => one.name)), { is_default: !INSTALLS.some((one) => one.connector_id === connectorId) });
                INSTALLS = [...INSTALLS, installHere];
            }
            if (fields && refuses(fields)) {
                await wait(600);
                return { kind: "refused", reason: "The provider did not recognise that store." };
            }
            /* The provider's page, stood in for: a moment, then whatever a
               real one would leave behind. */
            await wait(1400);
            const signedOut = accounts().find((one) => one.authConfigId === installHere!.id && !one.usable && one.status.toUpperCase() !== "CONNECTED");
            if (signedOut) {
                patchAccount(signedOut.id, { status: "CONNECTED" });
            } else {
                const store = typeof fields?.subdomain === "string" ? fields.subdomain : null;
                ACCOUNTS = [...ACCOUNTS, {
                    id: nextId("acc"), connector_id: connectorId, auth_config_id: installHere.id,
                    display_name: store ? `${store}.myshopify.example` : connectorId === "github" ? "acme-design" : "Acme",
                    status: "CONNECTED",
                    /* A GitHub App is authorised, and installed nowhere yet. */
                    install_state: connectorId === "github" ? "INSTALL_REQUIRED" : "READY",
                    created_at: new Date().toISOString(),
                }];
            }
            const pending: PendingSignIn = { connectorId, authConfigId: installHere.id, before };
            return readSignIn(pending, accounts());
        },

        async checkSignIn(_orgId, pending) {
            await wait(400);
            return readSignIn(pending, accounts());
        },

        async createAccount(_orgId, installId, credentials) {
            await wait(700);
            const held = INSTALLS.find((one) => one.id === installId);
            if (!held) throw new Error("That install is no longer here.");
            if (refuses(credentials)) throw new Error(`${entryOf(held.connector_id).title} refused that credential. Check it and try again.`);
            const id = nextId("acc");
            ACCOUNTS = [...ACCOUNTS, {
                id, connector_id: held.connector_id, auth_config_id: held.id,
                display_name: typeof credentials.username === "string" ? credentials.username : null,
                status: "CONNECTED", install_state: "READY", created_at: new Date().toISOString(),
            }];
            return id;
        },

        async disconnect(_orgId, accountId) {
            await wait(500);
            ACCOUNTS = ACCOUNTS.filter((raw) => raw.id !== accountId);
        },

        async rotate(_orgId, accountId, credentials) {
            await wait(700);
            if (refuses(credentials)) throw new Error("That credential was refused. Check it and try again.");
            patchAccount(accountId, { status: "CONNECTED" });
        },

        async finishInstall(_orgId, accountId): Promise<InstallStep> {
            await wait(500);
            const account = accounts().find((one) => one.id === accountId);
            if (!account) throw new Error("That account is no longer here.");
            const state = account.installState.toUpperCase();
            if (state === "READY") return { kind: "installed" };
            if (state === "CHOOSE_INSTALL") return { kind: "choices", choices: CHOICES[accountId] ?? [] };
            /* GitHub's install page, stood in for. */
            await wait(1200);
            patchAccount(accountId, { install_state: "READY" });
            return { kind: "installed" };
        },

        async bindInstallation(_orgId, accountId, installationId) {
            await wait(500);
            const choice = (CHOICES[accountId] ?? []).find((one) => one.installationId === installationId);
            if (!choice) throw new Error("That installation is not one this account reaches.");
            patchAccount(accountId, { install_state: "READY" });
        },

        async createInstall(_orgId, input) {
            await wait(600);
            const entry = entryOf(input.connectorId);
            const name = input.name ?? freshInstallName(input.connectorId, INSTALLS.map((one) => one.name));
            if (INSTALLS.some((one) => one.name === name)) throw new Error(`An install called ${name} is already here. Pick another name.`);
            if (privateAddress(input.config)) throw new Error("Refused: that address is private or loopback, and not allowed.");
            const kind = kindNamed(entry, input.kind ?? null) ?? primaryKind(entry);
            const signsIn = kind?.auth_scheme === "OAUTH2" || (kind?.kind === "mcp" && /oauth/i.test(where(input.config)));
            const made: Install = {
                id: nextId("ac"), connector_id: input.connectorId, kind: kind?.kind ?? "http", name,
                status: "ACTIVE", is_default: !INSTALLS.some((one) => one.connector_id === input.connectorId),
                config_source: input.own ? "ORG_CUSTOM" : "SYSTEM_DEFAULT",
                auth_scheme: signsIn ? "OAUTH2" : (kind?.auth_scheme ?? "API_KEY"),
                config: masked(input.config, entry, kind?.kind),
            };
            INSTALLS = [...INSTALLS, made];
            return { ...made };
        },

        async updateInstall(_orgId, wanted, change) {
            await wait(700);
            const held = INSTALLS.find((one) => one.id === wanted.id);
            if (!held) throw new Error("That install is no longer here.");
            if (change.name && change.name !== held.name && INSTALLS.some((one) => one.name === change.name)) throw new Error(`An install called ${change.name} is already here.`);
            if (privateAddress(change.config)) throw new Error("Refused: that address is private or loopback, and not allowed.");
            const entry = entryOf(held.connector_id);
            const config = change.config ? merged(held.config, change.config) : held.config;
            const moved = Boolean(change.config) && (where(config) !== where(held.config)
                || (held.config_source === "ORG_CUSTOM" && config?.client_id !== held.config?.client_id));
            const next: Install = { ...held, name: change.name ?? held.name, config: masked(config ?? undefined, entry, held.kind) };
            INSTALLS = INSTALLS.map((one) => (one.id === held.id ? next : one));
            let reauth = 0;
            if (moved) {
                for (const account of accounts().filter((one) => one.authConfigId === held.id)) {
                    patchAccount(account.id, { status: "REAUTH_REQUIRED" });
                    reauth += 1;
                }
            }
            const addressed = isBringYourOwn(entry);
            const discovery = moved && addressed
                ? /down/i.test(where(config)) ? { status: "failed", count: 0, reason: "CONNECTOR_DISCOVERY_FAILED" } : { status: "ok", count: held.kind === "mcp" ? 14 : 37, reason: null }
                : null;
            return { install: { ...next }, discovery, reauth };
        },

        async makeDefault(_orgId, wanted) {
            await wait(400);
            INSTALLS = INSTALLS.map((one) => (one.connector_id === wanted.connector_id ? { ...one, is_default: one.id === wanted.id } : one));
        },

        async refreshOperations(_orgId, wanted) {
            await wait(900);
            const held = INSTALLS.find((one) => one.id === wanted.id) ?? wanted;
            if (held.kind === "sql") return { status: "not_applicable", count: 0, reason: null };
            if (/down/i.test(where(held.config))) return { status: "failed", count: 0, reason: "CONNECTOR_DISCOVERY_FAILED" };
            return { status: "ok", count: held.kind === "mcp" ? 14 : 37, reason: null };
        },

        async deleteInstall(_orgId, wanted) {
            await wait(600);
            INSTALLS = INSTALLS.filter((one) => one.id !== wanted.id);
            ACCOUNTS = ACCOUNTS.filter((raw) => raw.auth_config_id !== wanted.id);
        },
    };
}
