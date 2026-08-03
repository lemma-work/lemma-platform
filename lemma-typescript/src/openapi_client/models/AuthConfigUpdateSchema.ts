/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AuthConfigUpdateSchema = {
    /**
     * Replacement configuration. Re-validated against the connector's schema, and re-checked against the network-target guard.
     */
    config?: (Record<string, any> | null);
    /**
     * Make this the install that a bare connector_id resolves to. Demotes whichever install currently holds that role.
     */
    is_default?: (boolean | null);
    /**
     * New name for this install. Accounts follow the rename, since they reference the install by id.
     */
    name?: (string | null);
    /**
     * ACTIVE or DISABLED.
     */
    status?: (string | null);
};
