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
     * Per-connection values the sign-in itself does not carry, such as Shopify's store `subdomain`. Validated against the connector kind's `config_schema`; omit for connectors that declare none.
     */
    connection_fields?: (Record<string, any> | null);
    /**
     * Connector ID to connect
     */
    connector_id?: (string | null);
    /**
     * Path inside the app to come back to when the flow finishes. Only a rooted path is accepted; anything else is ignored.
     */
    return_to?: (string | null);
};
