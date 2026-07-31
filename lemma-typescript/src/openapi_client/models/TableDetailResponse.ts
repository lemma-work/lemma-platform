/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ColumnSchema } from './ColumnSchema.js';
/**
 * Schema for table detail response.
 */
export type TableDetailResponse = {
    allowed_actions?: Array<string>;
    columns: Array<ColumnSchema>;
    config: (Record<string, any> | null);
    created_at: string;
    enable_rls: boolean;
    id: string;
    name: string;
    pod_id: string;
    primary_key_column: string;
    updated_at: string;
    visibility?: string;
};
