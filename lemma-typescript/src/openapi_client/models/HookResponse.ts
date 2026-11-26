/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type HookResponse = {
    created_at: string;
    /**
     * The member each row is added as.
     */
    created_by: string;
    enabled: boolean;
    /**
     * Column name to a dotted path into the posted JSON (`email`, `form_response.hidden.ref`, `answers.0.text`; `$` is the whole body). Leave it out to write the whole body to the table's `payload` JSON column. Either way `received_at` and `hook` are filled when the table has them.
     */
    fields: (Record<string, string> | null);
    id: string;
    /**
     * Why the last refused delivery was refused, as its sender was told, or that a duplicate added no row. Cleared when a row next arrives.
     */
    last_error: (string | null);
    last_error_at: (string | null);
    /**
     * When a delivery last added a row.
     */
    last_received_at: (string | null);
    name: string;
    table: string;
};
