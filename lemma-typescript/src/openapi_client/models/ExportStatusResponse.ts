/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExportProgressResponse } from './ExportProgressResponse.js';
import type { ExportStatus } from './ExportStatus.js';
/**
 * Status of a pod export job (pure Redis read).
 */
export type ExportStatusResponse = {
    bundle_filename?: (string | null);
    /**
     * Relative download path; present once the export is READY.
     */
    download_url?: (string | null);
    error?: (string | null);
    export_id: string;
    progress?: ExportProgressResponse;
    status: ExportStatus;
};

