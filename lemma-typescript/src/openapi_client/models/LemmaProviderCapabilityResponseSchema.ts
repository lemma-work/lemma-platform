/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuthScheme } from './AuthScheme.js';
import type { OAuth2DefaultsResponseSchema } from './OAuth2DefaultsResponseSchema.js';
export type LemmaProviderCapabilityResponseSchema = {
    auth_config_schema?: (Record<string, any> | null);
    auth_scheme?: AuthScheme;
    credential_schema?: (Record<string, any> | null);
    oauth2_defaults?: (OAuth2DefaultsResponseSchema | null);
    package_name?: (string | null);
    provider?: string;
    supports_org_custom_oauth?: boolean;
    system_default_available?: boolean;
};
