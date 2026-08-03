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

export const supportsCustomConfig = (capability: ConnectorKindSpec | null): boolean => {
    if (!capability) return false;
    const hasConfigFields = schemaHasFields(getConfigSchema(capability));
    if ('supports_org_custom_oauth' in capability) {
        return Boolean(capability.supports_org_custom_oauth && hasConfigFields);
    }
    if ('supports_org_custom_auth_config' in capability) {
        return Boolean(capability.supports_org_custom_auth_config && hasConfigFields);
    }
    return hasConfigFields;
};

/** True when this connector has any Advanced (non-default kind / custom config) option worth surfacing. */
export const hasAdvancedOptions = (app: Connector | null | undefined): boolean => {
    if (getSupportedKinds(app).length > 1) return true;
    return getKindSpecs(app).some((capability) => supportsCustomConfig(capability));
};

export const formatKindName = (kind: string): string => {
    if (kind === KIND.PACKAGE) return 'Native';
    if (kind === KIND.COMPOSIO) return 'Composio';
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
    if (kind === KIND.COMPOSIO) return 'Composio-managed auth with trigger-backed workflows. Recommended.';
    if (usesDirectCredentials(capability)) return 'Connect with credentials from this app, such as an API key or bot token.';
    if (kind === KIND.PACKAGE) return 'Use OAuth with Lemma-managed or organization-managed credentials.';
    return 'Use this kind for the connector connection.';
};

export const getManagedConfigCopy = (kind: string, capability: ConnectorKindSpec | null): string => {
    if (usesDirectCredentials(capability)) return 'Use the default credential setup for this app. Account credentials are added after enabling it.';
    if (kind === KIND.COMPOSIO) return 'Composio uses the system default configuration and supports triggers.';
    if (kind === KIND.PACKAGE) return 'Use the system default OAuth configuration for this app.';
    return `Use the default ${formatKindName(kind)} auth configuration for this app.`;
};

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

export const findAuthConfigForAccount = (
    account: Account,
    authConfigs: AuthConfig[] | undefined,
): AuthConfig | null =>
    (authConfigs || []).find((config) => config.id === account.auth_config_id) ??
    (authConfigs || []).find(
        (config) => config.connector_id === account.connector_id && config.status === 'ACTIVE',
    ) ??
    null;
