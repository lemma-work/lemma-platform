import type { Account, AuthConfig, Connector } from '@/lib/types';
import { buildSchemaFormFields, type JsonSchemaLike } from 'lemma-sdk';

export type ConnectorKindSpec = NonNullable<Connector['kinds']>[number];
export type SchemaValues = Record<string, unknown>;
export type AuthConfigMode = 'MANAGED' | 'CUSTOM';

export const KIND = {
    PACKAGE: 'package',
    COMPOSIO: 'composio',
    HTTP: 'http',
    SQL: 'sql',
    MCP: 'mcp',
} as const;

/**
 * The kinds where *the org supplies the address*.
 *
 * Every other kind is fully described by the catalog — Slack is Slack, and an
 * install of it needs nothing but credentials. These three are nothing until
 * someone says which host, which is why they need a config form at all and why
 * an org legitimately holds several installs of one of them.
 */
export const TENANT_CONFIGURED_KINDS: ReadonlySet<string> = new Set([
    KIND.HTTP,
    KIND.SQL,
    KIND.MCP,
]);

export const ACCOUNT_STATUS = {
    CONNECTED: 'CONNECTED',
    REAUTH_REQUIRED: 'REAUTH_REQUIRED',
    DISCONNECTED: 'DISCONNECTED',
} as const;

const isRecord = (value: unknown): value is Record<string, unknown> =>
    Boolean(value && typeof value === 'object' && !Array.isArray(value));

export const getAppLabel = (app: Connector | null | undefined) =>
    app?.title || app?.name || app?.id || 'this app';

export const getKindSpecs = (app: Connector | null | undefined): ConnectorKindSpec[] =>
    (app?.kinds || []) as ConnectorKindSpec[];

export const getSupportedKinds = (app: Connector | null | undefined): string[] => {
    const kinds = getKindSpecs(app)
        .map((capability) => String(capability.kind ?? ''))
        .filter((kind) => kind.length > 0);
    return kinds.length > 0 ? kinds : [KIND.PACKAGE];
};

export const getKindSpec = (
    app: Connector | null | undefined,
    kind: string | null | undefined,
): ConnectorKindSpec | null =>
    getKindSpecs(app).find((capability) => capability.kind === kind) ?? null;

/**
 * Composio-first: when a connector exposes a Composio capability we prefer it as
 * the default connect path. Native (Lemma) auth stays available under Advanced.
 */
export const getPrimaryKindSpec = (app: Connector | null | undefined): ConnectorKindSpec | null => {
    const capabilities = getKindSpecs(app);
    return (
        capabilities.find((capability) => capability.kind === KIND.COMPOSIO) ??
        capabilities[0] ??
        null
    );
};

export const getPrimaryKind = (app: Connector | null | undefined): string =>
    getPrimaryKindSpec(app)?.kind || getSupportedKinds(app)[0] || KIND.PACKAGE;

export const getConfigSchema = (capability: ConnectorKindSpec | null): JsonSchemaLike | null => {
    const schema = capability?.config_schema;
    return isRecord(schema) ? (schema as JsonSchemaLike) : null;
};

export const usesDirectCredentials = (capability: ConnectorKindSpec | null): boolean => {
    if (!capability) return false;
    if (capability.auth_scheme === 'API_KEY' || capability.auth_scheme === 'NOAUTH') return true;
    const direct = 'credential_schema' in capability ? capability.credential_schema : null;
    return isRecord(direct);
};

/**
 * Resolves the credential form for direct-credential (API key / bot token) apps.
 * Native Lemma apps carry it on `credential_schema`; Composio non-OAuth toolkits
 * expose the derived initiation fields on `auth_config_schema`.
 */
export const getCredentialSchema = (capability: ConnectorKindSpec | null): JsonSchemaLike | null => {
    if (!capability) return null;
    const direct = 'credential_schema' in capability ? capability.credential_schema : null;
    if (isRecord(direct)) return direct as JsonSchemaLike;
    if (usesDirectCredentials(capability)) {
        return getConfigSchema(capability);
    }
    return null;
};

export const schemaHasFields = (schema: JsonSchemaLike | null): boolean =>
    buildSchemaFormFields(schema).length > 0;

export const hasSystemDefault = (capability: ConnectorKindSpec | null): boolean =>
    Boolean(capability?.system_default_available);

export const isTenantConfigured = (capability: ConnectorKindSpec | null): boolean =>
    Boolean(capability && TENANT_CONFIGURED_KINDS.has(String(capability.kind)));

/**
 * True when an install of this kind cannot exist until the org fills in a config.
 *
 * `system_default_available` does not answer this. The catalog importer sets it
 * from `auth_method != OAUTH2`, which is a statement about who owns the OAuth
 * client — so a SQL connector, which has no OAuth client to own, reads as
 * "ready to connect" and the enable call goes out with no host in it.
 */
export const requiresInstallConfig = (capability: ConnectorKindSpec | null): boolean => {
    if (!capability) return false;
    // A tenant-configured kind is nothing without its address, so any config
    // field at all is worth asking for before the install is created.
    if (isTenantConfigured(capability)) return schemaHasFields(getConfigSchema(capability));
    // For an OAuth kind the config schema describes the org's own OAuth app —
    // opt-in, and unnecessary when the platform's client is available. This
    // mirrors the backend, which validates an OAuth system-default install
    // against an empty schema rather than this one.
    if (capability.auth_scheme === 'OAUTH2' && hasSystemDefault(capability)) return false;
    return buildSchemaFormFields(getConfigSchema(capability)).some((field) => field.required);
};

/** True when Connect can go straight to credentials (or OAuth) without a config form. */
export const canConnectWithDefaults = (capability: ConnectorKindSpec | null): boolean =>
    hasSystemDefault(capability) && !requiresInstallConfig(capability);

export const supportsCustomConfig = (capability: ConnectorKindSpec | null): boolean => {
    if (!capability) return false;
    const hasConfigFields = schemaHasFields(getConfigSchema(capability));
    if (!hasConfigFields) return false;
    // For an OAuth kind the config schema describes the org's *own OAuth app*,
    // which is opt-in and flagged. For every other kind the config schema
    // describes the connection itself, so having fields is the whole condition
    // — gating those on an OAuth flag left sql/mcp/http with no way in at all.
    if (capability.auth_scheme !== 'OAUTH2') return true;
    if ('supports_org_custom_oauth' in capability) {
        return Boolean(capability.supports_org_custom_oauth);
    }
    if ('supports_org_custom_auth_config' in capability) {
        return Boolean(capability.supports_org_custom_auth_config);
    }
    return true;
};

/** True when this connector has any Advanced (non-default kind / custom config) option worth surfacing. */
export const hasAdvancedOptions = (app: Connector | null | undefined): boolean => {
    if (getSupportedKinds(app).length > 1) return true;
    return getKindSpecs(app).some((capability) => supportsCustomConfig(capability));
};

/** Connectors whose install the org configures itself — databases, APIs, MCP servers. */
export const getTenantConfiguredConnectors = (
    connectors: Connector[] | undefined,
): Connector[] =>
    (connectors || []).filter((app) =>
        getKindSpecs(app).some((capability) => isTenantConfigured(capability)),
    );

export const getTenantConfiguredKindSpec = (
    app: Connector | null | undefined,
): ConnectorKindSpec | null =>
    getKindSpecs(app).find((capability) => isTenantConfigured(capability)) ?? null;

export const formatKindName = (kind: string): string => {
    if (kind === KIND.PACKAGE) return 'Native';
    if (kind === KIND.COMPOSIO) return 'Composio';
    if (kind === KIND.SQL) return 'Database';
    if (kind === KIND.HTTP) return 'API';
    if (kind === KIND.MCP) return 'MCP server';
    return kind
        .toLowerCase()
        .split('_')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
};

export const getKindLabel = (kind: string, capability: ConnectorKindSpec | null): string => {
    if (kind === KIND.COMPOSIO) return 'Composio (recommended)';
    if (kind === KIND.PACKAGE && usesDirectCredentials(capability)) return 'Native credentials';
    if (kind === KIND.PACKAGE) return 'Native OAuth';
    return formatKindName(kind);
};

export const getKindDescription = (kind: string, capability: ConnectorKindSpec | null): string => {
    if (kind === KIND.COMPOSIO) return 'Composio handles signing in, and supports triggers. Recommended.';
    if (kind === KIND.SQL) return 'Point Lemma at a PostgreSQL database and run read-only queries against it.';
    if (kind === KIND.HTTP) return 'Point Lemma at an OpenAPI spec; its endpoints become operations.';
    if (kind === KIND.MCP) return 'Point Lemma at an MCP server; its tools become operations.';
    if (usesDirectCredentials(capability)) return 'Connect with a key or token from the app itself.';
    if (kind === KIND.PACKAGE) return 'Sign in with Lemma’s app, or with your own.';
    return 'Another way to connect this.';
};

/**
 * The one-line version, for the entry-point cards.
 *
 * Separate from `getKindDescription` because the card and the dialog want
 * different lengths: the card sits three-across and any full sentence truncates,
 * while the dialog has the room to say what the thing actually is.
 */
export const getKindTagline = (kind: string): string => {
    if (kind === KIND.SQL) return 'PostgreSQL, read-only';
    if (kind === KIND.HTTP) return 'Endpoints from a spec';
    if (kind === KIND.MCP) return 'Tools from a server';
    return 'A connection you configure';
};

export const getManagedConfigCopy = (kind: string, capability: ConnectorKindSpec | null): string => {
    if (isTenantConfigured(capability)) return 'This one needs an address. Fill in the fields below.';
    if (usesDirectCredentials(capability)) return 'Nothing to set up here — you’ll add the account’s details next.';
    if (kind === KIND.COMPOSIO) return 'Composio handles this one. Nothing to set up.';
    if (kind === KIND.PACKAGE) return 'Sign in with Lemma’s own app. Nothing to set up.';
    return 'Use Lemma’s default setup for this.';
};

/**
 * The address an install points at, for the row that lists it.
 *
 * Two databases named "replica" are told apart by this line and nothing else,
 * so it reads the config the org actually typed rather than restating the kind.
 * Config comes back from the API redacted, but only the OAuth secrets are —
 * hosts and URLs survive.
 */
export const describeInstallTarget = (
    kind: string | null | undefined,
    config: Record<string, unknown> | null | undefined,
): string | null => {
    if (!isRecord(config)) return null;
    const text = (key: string): string | null => {
        const value = config[key];
        return typeof value === 'string' && value.trim() ? value.trim() : null;
    };

    if (kind === KIND.SQL) {
        const host = text('host');
        if (!host) return null;
        const port = config.port;
        const hostPort = typeof port === 'number' && port !== 5432 ? `${host}:${port}` : host;
        const database = text('database');
        return database ? `${hostPort}/${database}` : hostPort;
    }
    return text('server_url') ?? text('spec_url');
};

/** What to call an install in a list. Falls back to the connector when unnamed. */
export const getInstallLabel = (
    install: AuthConfig,
    connector: Connector | null | undefined,
): string =>
    install.name && install.name !== install.connector_id
        ? install.name
        : getAppLabel(connector);

export interface AccountStatusMeta {
    label: string;
    variant: 'success' | 'warning' | 'error';
    needsAttention: boolean;
    hint: string;
}

export const getAccountStatusMeta = (status: string | null | undefined): AccountStatusMeta => {
    switch (status) {
        case ACCOUNT_STATUS.REAUTH_REQUIRED:
            return {
                label: 'Reconnect needed',
                variant: 'warning',
                needsAttention: true,
                hint: 'This account’s credentials stopped working. Reconnect to restore access.',
            };
        case ACCOUNT_STATUS.DISCONNECTED:
            return {
                label: 'Disconnected',
                variant: 'error',
                needsAttention: true,
                hint: 'This account is disconnected. Reconnect to use it again.',
            };
        case ACCOUNT_STATUS.CONNECTED:
        default:
            return {
                label: 'Connected',
                variant: 'success',
                needsAttention: false,
                hint: 'This account is connected and ready to use.',
            };
    }
};

/**
 * The message to show when the API refuses an install.
 *
 * Worth unwrapping rather than showing "Failed to save": the two failures a
 * user actually hits here are a field the schema rejected and the network-target
 * guard refusing a private address, and both are things they can act on. The
 * envelope is `{message, code, details}`; `details.violations` carries the
 * offending field paths for a schema failure.
 */
export const describeConnectorError = (error: unknown, fallback: string): string => {
    const body = isRecord(error) && isRecord(error.body) ? error.body : null;
    if (!body) return error instanceof Error && error.message ? error.message : fallback;

    const message = typeof body.message === 'string' && body.message ? body.message : fallback;
    const details = isRecord(body.details) ? body.details : null;
    const violations = Array.isArray(details?.violations) ? details.violations : [];
    const firstViolation = violations.find(isRecord);
    if (firstViolation) {
        const path = typeof firstViolation.path === 'string' ? firstViolation.path : null;
        const reason = typeof firstViolation.message === 'string' ? firstViolation.message : null;
        if (reason) return path && path !== '(root)' ? `${path}: ${reason}` : reason;
    }
    return message;
};

/** Active installs for one connector, default first — the order the list shows. */
export const getInstallsForConnector = (
    authConfigs: AuthConfig[] | undefined,
    connectorId: string,
): AuthConfig[] =>
    (authConfigs || [])
        .filter((config) => config.connector_id === connectorId && config.status === 'ACTIVE')
        .sort((a, b) => Number(Boolean(b.is_default)) - Number(Boolean(a.is_default)));

export const findAuthConfigForAccount = (
    account: Account,
    authConfigs: AuthConfig[] | undefined,
): AuthConfig | null =>
    (authConfigs || []).find((config) => config.id === account.auth_config_id) ??
    (authConfigs || []).find(
        (config) => config.connector_id === account.connector_id && config.status === 'ACTIVE',
    ) ??
    null;
