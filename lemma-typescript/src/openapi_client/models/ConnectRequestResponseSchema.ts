/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for connect request response.
 *
 * `attributes` is deliberately absent. The row carries the live OAuth
 * `state`, the provider's own handle on the authorization, and the PKCE
 * verifier -- the three things that have to survive the redirect and the
 * three the caller has no use for. Returning them put the verifier and the
 * `state` into browser memory, client-side logs and any HAR capture, which is
 * the exposure PKCE exists to survive. The client needs `authorization_url`
 * and `id`.
 */
export type ConnectRequestResponseSchema = {
    auth_config_id: string;
    authorization_url: (string | null);
    connector_id: string;
    created_at: string;
    id: string;
    organization_id: string;
    status: string;
    updated_at: string;
    user_id: string;
};
