/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectorResponseSchema } from './ConnectorResponseSchema.js';
/**
 * Schema for account response.
 */
export type AccountResponseSchema = {
    allowed_scopes: (Array<string> | null);
    auth_config_id: string;
    connector?: (ConnectorResponseSchema | null);
    connector_id: string;
    created_at: string;
    display_name?: (string | null);
    email: (string | null);
    id: string;
    is_default?: boolean;
    organization_id: string;
    preferences: (Record<string, any> | null);
    provider?: (string | null);
    provider_account_id?: (string | null);
    status: string;
    updated_at: string;
    user_id: string;
};

