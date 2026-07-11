/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuthScheme } from './AuthScheme.js';
export type ComposioProviderCapabilityResponseSchema = {
    auth_config_schema?: (Record<string, any> | null);
    auth_scheme?: AuthScheme;
    provider?: string;
    supports_org_custom_auth_config?: boolean;
    system_default_available?: boolean;
    toolkit_slug: string;
};
