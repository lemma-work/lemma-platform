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
     * Of a contact-owned table, the columns a contact may read of their own rows -- an explicit choice, so a column added later stays members-only until it is chosen too. Omit to leave them unchanged.
     */
    contact_columns?: (Array<string> | null);
    /**
     * Make the table contact-owned, or stop it being. Enabling adds a `contact_id` column if there is none (rows without one are seen by members only) and requires `contact_columns`. Omit to leave it unchanged.
     */
    contact_owned?: (boolean | null);
    /**
     * Toggle per-user row-level security. Only allowed on an empty table: enabling adds the user_id ownership column and isolation policy, disabling removes the policy. Omit to leave RLS unchanged.
     */
    enable_rls?: (boolean | null);
    visibility?: (string | null);
};
