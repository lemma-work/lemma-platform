/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionPermissionsResponse } from './FunctionPermissionsResponse.js';
import type { FunctionStatus } from './FunctionStatus.js';
import type { FunctionType } from './FunctionType.js';
import type { JsonObject } from './JsonObject.js';
export type FunctionDetailResponse = {
    allowed_actions?: Array<string>;
    code?: (string | null);
    code_path?: (string | null);
    config?: (JsonObject | null);
    /**
     * Optional configuration schema derived from the function code.
     */
    config_schema?: (JsonObject | null);
    created_at: (string | null);
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    /**
     * Input JSON schema derived from the function code.
     */
    input_schema: JsonObject;
    name: string;
    /**
     * Output JSON schema derived from the function code.
     */
    output_schema: JsonObject;
    permissions: FunctionPermissionsResponse;
    pod_id: string;
    revision_hash?: (string | null);
    status: FunctionStatus;
    type: FunctionType;
    updated_at: (string | null);
    user_id: string;
    visibility?: string;
};
