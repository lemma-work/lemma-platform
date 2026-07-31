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
     * Signed, authenticated download URL; present once the export is READY. Requires a logged-in lemma user to fetch.
     */
    download_url?: (string | null);
    error?: (string | null);
    /**
     * When the download URL (and archive) expires.
     */
    expires_at?: (string | null);
    export_id: string;
    progress?: ExportProgressResponse;
    status: ExportStatus;
    /**
     * Data/asset-cap notices (e.g. truncated seed rows, skipped files).
     */
    warnings?: Array<string>;
};
