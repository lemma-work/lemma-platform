/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for updating a table.
 */
export type UpdateTableRequest = {
    /**
     * Replacement metadata/config payload for the table.
     */
    config?: (Record<string, any> | null);
    /**
     * Toggle per-user row-level security. Only allowed on an empty table: enabling adds the user_id ownership column and isolation policy, disabling removes the policy. Omit to leave RLS unchanged.
     */
    enable_rls?: (boolean | null);
    visibility?: (string | null);
};
