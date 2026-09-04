import { describe, expect, it } from 'vitest';
import { AuthScheme, ConnectorKind } from 'lemma-sdk';

import type { Connector } from '@/lib/types';
import {
    canConnectWithDefaults,
    describeConnectorError,
    describeInstallTarget,
    getCredentialSchema,
    getKindDescription,
    getKindLabel,
    getManagedConfigCopy,
    getTenantConfiguredConnectors,
    getTenantConfiguredKindSpec,
    hasSystemDefault,
    isTenantConfigured,
    installUsesOAuth,
    requiresInstallConfig,
    supportsCustomConfig,
} from './connector-utils';

type KindSpec = NonNullable<Connector['kinds']>[number];

/** A catalog row shaped the way the importer emits one. */
const connector = (id: string, kinds: Partial<KindSpec>[]): Connector =>
    ({ id, title: id, kinds: kinds as KindSpec[] }) as Connector;

/** `sql`, as it arrives once the importer carries the catalog's own kind. */
const sqlKind = {
    kind: ConnectorKind.SQL,
    auth_scheme: AuthScheme.API_KEY,
    // The importer sets this from `auth_method != OAUTH2`, so it is true here
    // despite there being no platform-owned anything to default to.
    system_default_available: true,
    supports_org_custom_oauth: false,
    config_schema: {
        type: 'object',
        required: ['dialect', 'host', 'database'],
        properties: {
            dialect: { type: 'string' },
            host: { type: 'string' },
            database: { type: 'string' },
        },
    },
    credential_schema: {
        type: 'object',
        required: ['username', 'password'],
        properties: { username: { type: 'string' }, password: { type: 'string' } },
    },
} satisfies Partial<KindSpec> as Partial<KindSpec>;

const mcpKind = {
    kind: ConnectorKind.MCP,
    auth_scheme: AuthScheme.API_KEY,
    system_default_available: true,
    supports_org_custom_oauth: false,
    config_schema: {
        type: 'object',
        required: ['server_url'],
        properties: { server_url: { type: 'string' } },
    },
} satisfies Partial<KindSpec> as Partial<KindSpec>;

/** A credential-only catalog app: a bot token and nothing to configure. */
const telegramKind = {
    kind: ConnectorKind.PACKAGE,
    auth_scheme: AuthScheme.API_KEY,
    system_default_available: true,
    supports_org_custom_oauth: false,
    config_schema: { type: 'object', properties: {}, additionalProperties: false },
    credential_schema: {
        type: 'object',
        required: ['bot_token'],
        properties: { bot_token: { type: 'string' } },
    },
} satisfies Partial<KindSpec> as Partial<KindSpec>;

const oauthKind = {
    kind: ConnectorKind.PACKAGE,
    auth_scheme: AuthScheme.OAUTH2,
    system_default_available: true,
    supports_org_custom_oauth: true,
    // The endpoints an org's own client id and secret are paired with. Without
    // them the credentials have nowhere to send anyone, which is why
    // `supportsCustomConfig` requires both halves — see the test below.
    oauth2_defaults: {
        authorization_url: 'https://example.com/oauth/authorize',
        token_url: 'https://example.com/oauth/token',
    },
    config_schema: {
        type: 'object',
        required: ['client_id', 'client_secret'],
        properties: { client_id: { type: 'string' }, client_secret: { type: 'string' } },
    },
} satisfies Partial<KindSpec> as Partial<KindSpec>;

/**
 * GitHub: the first first-party OAuth connector served over the `http` kind.
 * Lemma owns the OAuth client, and the operations carry their own server_url,
 * so there is nothing for an org to fill in — despite `http` being one of the
 * kinds an org normally has to address itself.
 */
const githubKind = {
    kind: ConnectorKind.HTTP,
    auth_scheme: AuthScheme.OAUTH2,
    system_default_available: true,
    supports_org_custom_oauth: true,
    // The endpoints an org's own client id and secret are paired with. Without
    // them the credentials have nowhere to send anyone, which is why
    // `supportsCustomConfig` requires both halves — see the test below.
    oauth2_defaults: {
        authorization_url: 'https://example.com/oauth/authorize',
        token_url: 'https://example.com/oauth/token',
    },
    config_schema: {
        type: 'object',
        required: ['client_id', 'client_secret'],
        properties: { client_id: { type: 'string' }, client_secret: { type: 'string' } },
    },
} satisfies Partial<KindSpec> as Partial<KindSpec>;

describe('an OAuth connector served over http', () => {
    it('connects in one click instead of demanding an OAuth app', () => {
        // The regression this guards: `http` is a tenant-configured kind, and
        // that check ran first — so GitHub asked every org for a client id and
        // secret that Lemma already has.
        expect(requiresInstallConfig(githubKind as KindSpec)).toBe(false);
        expect(canConnectWithDefaults(githubKind as KindSpec)).toBe(true);
    });

    it('still asks for an OAuth app when the platform has no client', () => {
        const noSystemClient = { ...githubKind, system_default_available: false };
        expect(requiresInstallConfig(noSystemClient as KindSpec)).toBe(true);
        expect(canConnectWithDefaults(noSystemClient as KindSpec)).toBe(false);
    });

    it('is described as OAuth, not as a spec to point at', () => {
        expect(getKindLabel(ConnectorKind.HTTP, githubKind as KindSpec)).toBe('Native OAuth');
        expect(getKindDescription(ConnectorKind.HTTP, githubKind as KindSpec))
            .not.toContain('OpenAPI');
        expect(getManagedConfigCopy(ConnectorKind.HTTP, githubKind as KindSpec))
            .not.toContain('needs an address');
    });

    it('leaves a bring-your-own http install describing a spec', () => {
        const byoApi = { ...githubKind, auth_scheme: AuthScheme.API_KEY };
        expect(getKindDescription(ConnectorKind.HTTP, byoApi as KindSpec)).toContain('OpenAPI');
        expect(requiresInstallConfig(byoApi as KindSpec)).toBe(true);
    });

    it('is not something the org supplies an address for', () => {
        // The regression this guards: `http` is a tenant-configured kind, so
        // GitHub was routed to the databases/APIs/MCP flow — Connect opened the
        // "add a connection" form asking for an address, and the
        // Lemma's-app-or-your-own choice was never reachable. Every other
        // GitHub assertion above passed the whole time, because none of them
        // went through this classifier.
        expect(isTenantConfigured(githubKind as KindSpec)).toBe(false);

        // Still true without a platform client: the org owes an OAuth app, not
        // an address, so it must not fall back into the connection flow.
        expect(
            isTenantConfigured({ ...githubKind, system_default_available: false } as KindSpec),
        ).toBe(false);

        // A genuine bring-your-own API over http is unchanged.
        expect(
            isTenantConfigured({ ...githubKind, auth_scheme: AuthScheme.API_KEY } as KindSpec),
        ).toBe(true);
    });

    it('stays in the catalog grid instead of the connections section', () => {
        const catalog = [
            connector('github', [githubKind]),
            connector('sql', [sqlKind]),
        ];
        expect(getTenantConfiguredConnectors(catalog).map((app) => app.id)).toEqual(['sql']);
        expect(getTenantConfiguredKindSpec(catalog[0])).toBeNull();
    });

    it('offers the Lemma-or-your-own choice', () => {
        // What the user actually sees: "Use Lemma's" with a "Use my own"
        // button beside it, rather than a form demanding a client id.
        expect(hasSystemDefault(githubKind as KindSpec)).toBe(true);
        expect(supportsCustomConfig(githubKind as KindSpec)).toBe(true);
    });
});

/**
 * A Composio toolkit that authenticates with the third party's own API key
 * (freshdesk, metabase, posthog...). Its `config_schema` is the *end user's*
 * credential form, derived by the catalog importer from Composio's
 * `connected_account_initiation` fields — not an org install config.
 */
const composioApiKeyKind = {
    kind: ConnectorKind.COMPOSIO,
    auth_scheme: AuthScheme.API_KEY,
    system_default_available: true,
    supports_org_custom_oauth: false,
    config_schema: {
        type: 'object',
        required: ['generic_api_key'],
        properties: { generic_api_key: { type: 'string' } },
    },
} satisfies Partial<KindSpec> as Partial<KindSpec>;

const composioOAuthKind = {
    kind: ConnectorKind.COMPOSIO,
    auth_scheme: AuthScheme.OAUTH2,
    system_default_available: true,
    supports_org_custom_oauth: false,
} satisfies Partial<KindSpec> as Partial<KindSpec>;

describe('a Composio toolkit', () => {
    it('goes straight to the credential form instead of Advanced setup', () => {
        // The regression this guards: the API-key form's required fields read
        // as an org install config, so Connect opened Advanced setup instead of
        // asking for the key.
        expect(requiresInstallConfig(composioApiKeyKind as KindSpec)).toBe(false);
        expect(canConnectWithDefaults(composioApiKeyKind as KindSpec)).toBe(true);
    });

    it('never offers "Use my own" — the backend rejects org credentials', () => {
        // Composio runs on Lemma's Composio account. Offering the choice
        // produced a button whose only outcome was a 400.
        expect(supportsCustomConfig(composioApiKeyKind as KindSpec)).toBe(false);
        expect(supportsCustomConfig(composioOAuthKind as KindSpec)).toBe(false);
    });

    it('still renders the account credential form', () => {
        // The fix must not reach the API-key connect dialog, which sources its
        // fields here rather than through the two predicates above.
        expect(getCredentialSchema(composioApiKeyKind as KindSpec)).toEqual(
            composioApiKeyKind.config_schema,
        );
    });

    it('leaves the OAuth toolkit connecting in one click, as before', () => {
        expect(requiresInstallConfig(composioOAuthKind as KindSpec)).toBe(false);
        expect(canConnectWithDefaults(composioOAuthKind as KindSpec)).toBe(true);
    });
});

describe('tenant-configured kinds', () => {
    it('recognises the three kinds whose address the org supplies', () => {
        expect(isTenantConfigured(sqlKind as KindSpec)).toBe(true);
        expect(isTenantConfigured(mcpKind as KindSpec)).toBe(true);
        expect(isTenantConfigured(telegramKind as KindSpec)).toBe(false);
        expect(isTenantConfigured(null)).toBe(false);
    });

    it('does not let system_default_available stand in for "ready to connect"', () => {
        // The regression this guards: Connect used to open the credential
        // dialog for a database, collect a password, and then enable the
        // install with no host in it.
        expect(canConnectWithDefaults(sqlKind as KindSpec)).toBe(false);
        expect(requiresInstallConfig(sqlKind as KindSpec)).toBe(true);

        // A bot token still goes straight to credentials — nothing to configure.
        expect(canConnectWithDefaults(telegramKind as KindSpec)).toBe(true);
        expect(requiresInstallConfig(telegramKind as KindSpec)).toBe(false);
    });

    it('offers a config form for non-OAuth kinds that have config fields', () => {
        // supportsCustomConfig used to read supports_org_custom_oauth, which
        // the importer sets only for OAuth2 — so these were unreachable.
        expect(supportsCustomConfig(sqlKind as KindSpec)).toBe(true);
        expect(supportsCustomConfig(mcpKind as KindSpec)).toBe(true);
    });

    it('still gates an org-owned OAuth app behind its own flag', () => {
        expect(supportsCustomConfig(oauthKind as KindSpec)).toBe(true);
        expect(
            supportsCustomConfig({ ...oauthKind, supports_org_custom_oauth: false } as KindSpec),
        ).toBe(false);
        // An OAuth install with a system default needs no config form up front.
        expect(canConnectWithDefaults(oauthKind as KindSpec)).toBe(true);
    });

    it('picks the tenant-configured connectors out of the catalog', () => {
        const catalog = [
            connector('slack', [oauthKind]),
            connector('sql', [sqlKind]),
            connector('mcp', [mcpKind]),
        ];
        expect(getTenantConfiguredConnectors(catalog).map((app) => app.id)).toEqual([
            'sql',
            'mcp',
        ]);
        expect(getTenantConfiguredKindSpec(catalog[1])?.kind).toBe('sql');
        expect(getTenantConfiguredKindSpec(catalog[0])).toBeNull();
    });
});

describe('describeInstallTarget', () => {
    it('reads a database install as host/database', () => {
        expect(
            describeInstallTarget('sql', { dialect: 'postgresql', host: 'db.internal', database: 'analytics' }),
        ).toBe('db.internal/analytics');
    });

    it('keeps a non-default port, which is what tells two hosts apart', () => {
        expect(describeInstallTarget('sql', { host: 'db.internal', port: 6543, database: 'analytics' })).toBe(
            'db.internal:6543/analytics',
        );
        expect(describeInstallTarget('sql', { host: 'db.internal', port: 5432, database: 'analytics' })).toBe(
            'db.internal/analytics',
        );
    });

    it('reads a server URL for mcp, and falls back to the spec URL for openapi', () => {
        expect(describeInstallTarget('mcp', { server_url: 'https://mcp.example.com/' })).toBe(
            'https://mcp.example.com/',
        );
        expect(describeInstallTarget('http', { spec_url: 'https://api.example.com/openapi.json' })).toBe(
            'https://api.example.com/openapi.json',
        );
    });

    it('says nothing rather than guessing when there is no config', () => {
        expect(describeInstallTarget('sql', null)).toBeNull();
        expect(describeInstallTarget('composio', { toolkit: 'slack' })).toBeNull();
    });
});

describe('describeConnectorError', () => {
    // The SDK's `ApiError` is flat and has never had a `body`, but this
    // function looked for one and bailed when it was absent -- which was
    // always. So the branch it exists for, unwrapping `details.violations`,
    // was unreachable, and a schema rejection rendered as the backend's
    // generic "Invalid install config." with no indication of which field.
    // There was no test for it at all.
    class ApiErrorLike extends Error {
        constructor(
            readonly statusCode: number,
            message: string,
            readonly code?: string,
            readonly details?: unknown,
        ) {
            super(message);
            this.name = 'ApiError';
        }
    }

    it('names the field the backend rejected', () => {
        const error = new ApiErrorLike(400, 'Invalid install config.', 'VALIDATION', {
            violations: [{ path: 'server_url', message: 'is not a valid URL' }],
        });

        expect(describeConnectorError(error, 'Failed')).toBe(
            'server_url: is not a valid URL',
        );
    });

    it('drops a (root) path rather than showing it to a person', () => {
        const error = new ApiErrorLike(400, 'Invalid install config.', 'VALIDATION', {
            violations: [{ path: '(root)', message: "'oauth' was unexpected" }],
        });

        expect(describeConnectorError(error, 'Failed')).toBe("'oauth' was unexpected");
    });

    it("keeps the SSRF guard's own explanation, which a user can act on", () => {
        const error = new ApiErrorLike(
            400,
            "Host 'x' resolves to an address that is not routable on the public internet.",
        );

        expect(describeConnectorError(error, 'Failed')).toContain('not routable');
    });

    it('falls back to the message when there are no violations', () => {
        const error = new ApiErrorLike(409, 'That name is already in use.');

        expect(describeConnectorError(error, 'Failed')).toBe('That name is already in use.');
    });

    it('still reads an envelope carried under body, if one ever is', () => {
        const error = { body: { message: 'Wrapped', details: { violations: [] } } };

        expect(describeConnectorError(error, 'Failed')).toBe('Wrapped');
    });

    it('falls back for something that is not an error at all', () => {
        expect(describeConnectorError(null, 'Failed')).toBe('Failed');
        expect(describeConnectorError('a string', 'Failed')).toBe('Failed');
    });
});


describe('an OAuth kind that promises an org app it cannot honour', () => {
    /**
     * Sixty of eighty-four connectors in one deployment carried a `package`
     * OAuth kind with `supports_org_custom_oauth: true`, no `oauth2_defaults`
     * and no platform client — Instagram among them. "Use my own" took a client
     * id and secret, created the install, and only then failed at sign-in with
     * "OAuth2 defaults are not configured", leaving the install behind and its
     * name taken.
     */
    const strandedKind = {
        kind: ConnectorKind.PACKAGE,
        auth_scheme: AuthScheme.OAUTH2,
        system_default_available: false,
        supports_org_custom_oauth: true,
        config_schema: {
            type: 'object',
            required: ['client_id', 'client_secret'],
            properties: { client_id: { type: 'string' }, client_secret: { type: 'string' } },
        },
    } satisfies Partial<KindSpec> as Partial<KindSpec>;

    it('is not offered a form whose only outcome is a stranded install', () => {
        expect(supportsCustomConfig(strandedKind as KindSpec)).toBe(false);
    });

    it('is offered one as soon as there are endpoints to pair the app with', () => {
        const usable = {
            ...strandedKind,
            oauth2_defaults: {
                authorization_url: 'https://example.com/oauth/authorize',
                token_url: 'https://example.com/oauth/token',
            },
        };

        expect(supportsCustomConfig(usable as KindSpec)).toBe(true);
    });
});

describe('how an install says it is connected', () => {
    /**
     * The catalog cannot answer this for `mcp`. One entry stands for every
     * server a tenant may point at, and they do not agree: the entry says
     * API_KEY, while an install whose server described its own authorization at
     * create time signs in through a browser. Reading the catalog instead
     * produced an account that looked connected, held no token, and 401'd every
     * call, with no control anywhere that offered to fix it.
     */
    const install = (overrides: Record<string, unknown> = {}) =>
        ({ id: 'i1', kind: 'mcp', connector_id: 'mcp', name: 'a-server', ...overrides }) as never;

    it('believes the install over the catalog', () => {
        expect(installUsesOAuth(install({ auth_scheme: 'OAUTH2' }), mcpKind as KindSpec)).toBe(true);
    });

    it('leaves a token install alone', () => {
        expect(installUsesOAuth(install({ auth_scheme: 'API_KEY' }), mcpKind as KindSpec)).toBe(
            false,
        );
    });

    it('falls back to the catalog when the install does not say', () => {
        // A deployment that predates the field. The catalog was the right
        // answer for every kind except this one.
        expect(installUsesOAuth(install(), mcpKind as KindSpec)).toBe(false);
        expect(installUsesOAuth(install(), oauthKind as KindSpec)).toBe(true);
    });

    it('answers for no install at all', () => {
        expect(installUsesOAuth(null, mcpKind as KindSpec)).toBe(false);
        expect(installUsesOAuth(undefined, null)).toBe(false);
    });
});
