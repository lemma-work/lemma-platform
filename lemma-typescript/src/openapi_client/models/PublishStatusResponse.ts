/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExportProgressResponse } from './ExportProgressResponse.js';
import type { PublishMode } from './PublishMode.js';
import type { PublishStatus } from './PublishStatus.js';
/**
 * Status of a pod publish job (pure Redis read).
 */
export type PublishStatusResponse = {
    account_id: (string | null);
    error?: (string | null);
    error_code?: (string | null);
    events_url: string;
    mode: PublishMode;
    pod_id: string;
    private: boolean;
    progress?: ExportProgressResponse;
    publish_id: string;
    repo_name: string;
    repo_url?: (string | null);
    retryable?: boolean;
    status: PublishStatus;
    warnings?: Array<string>;
};
