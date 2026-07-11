/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Lean table shape for list responses.
 *
 * Omits the full `columns` definitions and `config` — fetch those from
 * `table.get`. Exposes a cheap `column_count` for list views.
 */
export type TableSummaryResponse = {
    allowed_actions?: Array<string>;
    column_count?: number;
    created_at: string;
    enable_rls: boolean;
    id: string;
    name: string;
    pod_id: string;
    primary_key_column: string;
    updated_at: string;
    visibility?: string;
};
