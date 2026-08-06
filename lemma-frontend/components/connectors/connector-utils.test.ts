import { describe, expect, it } from 'vitest';
import { AuthScheme, ConnectorKind } from 'lemma-sdk';

import type { Connector } from '@/lib/types';
import {
    canConnectWithDefaults,
    describeInstallTarget,
    getTenantConfiguredConnectors,
    getTenantConfiguredKindSpec,
    isTenantConfigured,
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
    config_schema: {
        type: 'object',
        required: ['client_id', 'client_secret'],
        properties: { client_id: { type: 'string' }, client_secret: { type: 'string' } },
    },
} satisfies Partial<KindSpec> as Partial<KindSpec>;

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
