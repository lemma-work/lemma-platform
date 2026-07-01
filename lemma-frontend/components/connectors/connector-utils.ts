import type { Account, AuthConfig, Connector } from '@/lib/types';
import { buildSchemaFormFields, type JsonSchemaLike } from 'lemma-sdk';

export type ProviderCapability = NonNullable<Connector['provider_capabilities']>[number];
export type SchemaValues = Record<string, unknown>;
export type AuthConfigMode = 'MANAGED' | 'CUSTOM';

export const PROVIDER = {
    LEMMA: 'LEMMA',
    COMPOSIO: 'COMPOSIO',
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

export const getProviderCapabilities = (app: Connector | null | undefined): ProviderCapability[] =>
    (app?.provider_capabilities || []) as ProviderCapability[];

export const getSupportedProviders = (app: Connector | null | undefined): string[] => {
    const providers = getProviderCapabilities(app)
        .map((capability) => capability.provider)
        .filter((provider): provider is string => typeof provider === 'string');
    return providers.length > 0 ? providers : [PROVIDER.LEMMA];
};

export const getProviderCapability = (
    app: Connector | null | undefined,
    provider: string | null | undefined,
): ProviderCapability | null =>
    getProviderCapabilities(app).find((capability) => capability.provider === provider) ?? null;

/**
 * Composio-first: when a connector exposes a Composio capability we prefer it as
 * the default connect path. Native (Lemma) auth stays available under Advanced.
 */
export const getPrimaryCapability = (app: Connector | null | undefined): ProviderCapability | null => {
    const capabilities = getProviderCapabilities(app);
    return (
        capabilities.find((capability) => capability.provider === PROVIDER.COMPOSIO) ??
        capabilities[0] ??
        null
    );
};

export const getPrimaryProvider = (app: Connector | null | undefined): string =>
    getPrimaryCapability(app)?.provider || getSupportedProviders(app)[0] || PROVIDER.LEMMA;

export const getAuthConfigSchema = (capability: ProviderCapability | null): JsonSchemaLike | null => {
    const schema = capability?.auth_config_schema;
    return isRecord(schema) ? (schema as JsonSchemaLike) : null;
};

export const usesDirectCredentials = (capability: ProviderCapability | null): boolean => {
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
export const getCredentialSchema = (capability: ProviderCapability | null): JsonSchemaLike | null => {
    if (!capability) return null;
    const direct = 'credential_schema' in capability ? capability.credential_schema : null;
    if (isRecord(direct)) return direct as JsonSchemaLike;
    if (usesDirectCredentials(capability)) {
        return getAuthConfigSchema(capability);
    }
    return null;
};

export const schemaHasFields = (schema: JsonSchemaLike | null): boolean =>
    buildSchemaFormFields(schema).length > 0;

export const hasSystemDefault = (capability: ProviderCapability | null): boolean =>
    Boolean(capability?.system_default_available);

export const supportsCustomConfig = (capability: ProviderCapability | null): boolean => {
    if (!capability) return false;
    const hasConfigFields = schemaHasFields(getAuthConfigSchema(capability));
    if ('supports_org_custom_oauth' in capability) {
        return Boolean(capability.supports_org_custom_oauth && hasConfigFields);
    }
    if ('supports_org_custom_auth_config' in capability) {
        return Boolean(capability.supports_org_custom_auth_config && hasConfigFields);
    }
    return hasConfigFields;
};

/** True when this connector has any Advanced (non-default provider / custom config) option worth surfacing. */
export const hasAdvancedOptions = (app: Connector | null | undefined): boolean => {
    if (getSupportedProviders(app).length > 1) return true;
    return getProviderCapabilities(app).some((capability) => supportsCustomConfig(capability));
};

export const formatProviderName = (provider: string): string => {
    if (provider === PROVIDER.LEMMA) return 'Native';
    if (provider === PROVIDER.COMPOSIO) return 'Composio';
    return provider
        .toLowerCase()
        .split('_')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
};

export const getProviderLabel = (provider: string, capability: ProviderCapability | null): string => {
    if (provider === PROVIDER.COMPOSIO) return 'Composio (recommended)';
    if (provider === PROVIDER.LEMMA && usesDirectCredentials(capability)) return 'Native credentials';
    if (provider === PROVIDER.LEMMA) return 'Native OAuth';
    return formatProviderName(provider);
};

export const getProviderDescription = (provider: string, capability: ProviderCapability | null): string => {
    if (provider === PROVIDER.COMPOSIO) return 'Composio-managed auth with trigger-backed workflows. Recommended.';
    if (usesDirectCredentials(capability)) return 'Connect with credentials from this app, such as an API key or bot token.';
    if (provider === PROVIDER.LEMMA) return 'Use OAuth with Lemma-managed or organization-managed credentials.';
    return 'Use this provider for the connector connection.';
};

export const getManagedConfigCopy = (provider: string, capability: ProviderCapability | null): string => {
    if (usesDirectCredentials(capability)) return 'Use the default credential setup for this app. Account credentials are added after enabling it.';
    if (provider === PROVIDER.COMPOSIO) return 'Composio uses the system default configuration and supports triggers.';
    if (provider === PROVIDER.LEMMA) return 'Use the system default OAuth configuration for this app.';
    return `Use the default ${formatProviderName(provider)} auth configuration for this app.`;
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
