/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuthScheme } from './AuthScheme.js';
import type { ConnectorKind } from './ConnectorKind.js';
import type { OAuth2DefaultsResponseSchema } from './OAuth2DefaultsResponseSchema.js';
/**
 * One way a connector can be installed.
 *
 * Flat rather than a union over the five kinds: a client's job here is to
 * decide what to put in an install's `config`, and for that `kind` plus the
 * schemas is the whole answer. The per-kind extras below are populated only
 * where they apply.
 */
export type ConnectorKindResponseSchema = {
    auth_scheme?: AuthScheme;
    /**
     * JSON Schema for an install's `config`.
     */
    config_schema?: (Record<string, any> | null);
    credential_schema?: (Record<string, any> | null);
    discovery?: string;
    kind: ConnectorKind;
    oauth2_defaults?: (OAuth2DefaultsResponseSchema | null);
    package_name?: (string | null);
    supports_org_custom_oauth?: boolean;
    system_default_available?: boolean;
    toolkit_slug?: (string | null);
};
