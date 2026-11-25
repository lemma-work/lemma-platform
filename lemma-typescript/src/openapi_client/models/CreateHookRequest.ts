/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreateHookRequest = {
    /**
     * Column name to a dotted path into the posted JSON (`email`, `form_response.hidden.ref`, `answers.0.text`; `$` is the whole body). Leave it out to write the whole body to the table's `payload` JSON column. Either way `received_at` and `hook` are filled when the table has them.
     */
    fields?: (Record<string, string> | null);
    /**
     * Lowercase letters, digits, '-' or '_'.
     */
    name: string;
    /**
     * The table each delivery adds a row to.
     */
    table: string;
};
