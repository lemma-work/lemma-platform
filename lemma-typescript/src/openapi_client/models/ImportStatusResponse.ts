/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExportProgressResponse } from './ExportProgressResponse.js';
import type { ImportPlanResponse } from './ImportPlanResponse.js';
import type { ImportStatus } from './ImportStatus.js';
/**
 * Status of a durable pod import job.
 */
export type ImportStatusResponse = {
    cancel_requested_at?: (string | null);
    committed_steps?: Array<number>;
    current_step?: (number | null);
    error?: (string | null);
    events_url: string;
    import_id: string;
    plan?: (ImportPlanResponse | null);
    pod_id: string;
    progress?: ExportProgressResponse;
    source_kind: string;
    status: ImportStatus;
};
