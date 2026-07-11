/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionPermissionsResponse } from './FunctionPermissionsResponse.js';
import type { FunctionStatus } from './FunctionStatus.js';
import type { FunctionType } from './FunctionType.js';
export type FunctionDetailResponse = {
    allowed_actions?: Array<string>;
    code?: (string | null);
    code_hash?: (string | null);
    code_path?: (string | null);
    config?: (Record<string, any> | null);
    /**
     * Optional configuration schema derived from the function code.
     */
    config_schema?: (Record<string, any> | null);
    created_at: any;
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    /**
     * Input JSON schema derived from the function code.
     */
    input_schema: Record<string, any>;
    name: string;
    /**
     * Output JSON schema derived from the function code.
     */
    output_schema: Record<string, any>;
    permissions: FunctionPermissionsResponse;
    pod_id: string;
    /**
     * pip dependencies declared in the code's #python_packages header.
     */
    python_packages?: Array<string>;
    status: FunctionStatus;
    type: FunctionType;
    updated_at: any;
    user_id: string;
    visibility?: string;
};
