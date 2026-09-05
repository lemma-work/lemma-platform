/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JsonObject } from './JsonObject.js';
/**
 * One entry in a function's revision history.
 */
export type FunctionRevisionResponse = {
    code?: (string | null);
    config_schema?: (JsonObject | null);
    created_at: (string | null);
    created_by?: (string | null);
    function_id: string;
    id: string;
    input_schema?: (JsonObject | null);
    /**
     * True for the revision this function runs.
     */
    is_live: boolean;
    label?: (string | null);
    output_schema?: (JsonObject | null);
    /**
     * Set when retention removed this revision's artifact. The entry stays in the history, but it can no longer be run or promoted.
     */
    pruned_at?: (string | null);
    revision_hash: string;
    revision_number: number;
};
