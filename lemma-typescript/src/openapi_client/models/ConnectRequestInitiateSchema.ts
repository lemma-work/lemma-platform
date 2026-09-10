/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for initiating a connect request.
 */
export type ConnectRequestInitiateSchema = {
    /**
     * Auth config ID to connect
     */
    auth_config_id?: (string | null);
    /**
     * Connector ID to connect
     */
    connector_id?: (string | null);
    /**
     * Path inside the app to come back to when the flow finishes. Only a rooted path is accepted; anything else is ignored.
     */
    return_to?: (string | null);
};
