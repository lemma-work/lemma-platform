/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for directly connecting a credential-managed native account.
 */
export type AccountCreateSchema = {
    allowed_scopes?: (Array<string> | null);
    /**
     * Auth config ID to connect
     */
    auth_config_id?: (string | null);
    /**
     * Auth config name to connect
     */
    auth_config_name?: (string | null);
    credentials?: Record<string, any>;
    email?: (string | null);
    preferences?: (Record<string, any> | null);
    provider_account_id?: (string | null);
};
