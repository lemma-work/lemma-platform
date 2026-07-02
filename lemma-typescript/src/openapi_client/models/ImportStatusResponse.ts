/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExportProgressResponse } from './ExportProgressResponse.js';
import type { ImportPlanResponse } from './ImportPlanResponse.js';
import type { ImportStatus } from './ImportStatus.js';
/**
 * Status of a pod import job (pure Redis read).
 */
export type ImportStatusResponse = {
    error?: (string | null);
    events_url: string;
    import_id: string;
    plan?: (ImportPlanResponse | null);
    pod_id: string;
    progress?: ExportProgressResponse;
    source_kind: string;
    status: ImportStatus;
};

