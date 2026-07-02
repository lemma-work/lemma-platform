/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExportProgressResponse } from './ExportProgressResponse.js';
import type { PublishStatus } from './PublishStatus.js';
/**
 * Status of a pod publish job (pure Redis read).
 */
export type PublishStatusResponse = {
    error?: (string | null);
    events_url: string;
    pod_id: string;
    progress?: ExportProgressResponse;
    publish_id: string;
    repo_name: string;
    repo_url?: (string | null);
    status: PublishStatus;
};

