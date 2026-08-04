/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionResourcePermissionResponse } from './FunctionResourcePermissionResponse.js';
import type { FunctionStatus } from './FunctionStatus.js';
import type { FunctionType } from './FunctionType.js';
import type { JsonObject } from './JsonObject.js';
/**
 * Lean function shape for list responses.
 *
 * Omits the heavy `input_schema` / `output_schema` / `config_schema` (full JSON
 * schemas derived from the function code) and `code` — fetch those from
 * `function.get`.
 */
export type FunctionSummaryResponse = {
    allowed_actions?: Array<string>;
    code_path?: (string | null);
    config?: (JsonObject | null);
    created_at: (string | null);
    description?: (string | null);
    grants?: (Array<FunctionResourcePermissionResponse> | null);
    icon_url?: (string | null);
    id: string;
    name: string;
    pod_id: string;
    revision_hash?: (string | null);
    status: FunctionStatus;
    type: FunctionType;
    updated_at: (string | null);
    user_id: string;
    visibility?: string;
};
