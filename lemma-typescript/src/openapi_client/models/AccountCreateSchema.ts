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
    /**
     * The Lemma (pod) this account is connected for. You must be a member. The pod's per-member connector for the installation is reused or declared, and the account becomes your default there; either step failing never fails the connect.
     */
    pod_id?: (string | null);
    preferences?: (Record<string, any> | null);
    provider_account_id?: (string | null);
};
