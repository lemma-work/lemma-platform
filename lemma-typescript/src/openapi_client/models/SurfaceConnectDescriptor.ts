/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuthScheme } from './AuthScheme.js';
/**
 * What the frontend needs to render the "connect an account" (CUSTOM) flow
 * for a surface's connector — a slim projection of the connector's LEMMA
 * capability. ``system_oauth_available`` means the platform supplies the OAuth
 * app so the user connects without registering their own (distinct from whether
 * a fully-managed SYSTEM bot exists — that's ``supported_credential_modes``).
 */
export type SurfaceConnectDescriptor = {
    auth_config_schema?: (Record<string, any> | null);
    auth_scheme: AuthScheme;
    credential_schema?: (Record<string, any> | null);
    supports_org_custom_oauth?: boolean;
    system_oauth_available?: boolean;
};

