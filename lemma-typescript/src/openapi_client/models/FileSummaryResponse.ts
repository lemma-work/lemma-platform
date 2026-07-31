/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Lean file/folder shape for list responses.
 *
 * Omits the heavy `metadata` (JSONB) and `last_processing_error` (can be
 * multi-KB) — fetch those from `file.get`. Keeps `description` (small, may be
 * shown in list views).
 */
export type FileSummaryResponse = {
    allowed_actions?: Array<string>;
    created_at: string;
    description: (string | null);
    id: string;
    indexed_at?: (string | null);
    kind: string;
    mime_type?: (string | null);
    name: string;
    owner_user_id?: (string | null);
    path: string;
    pod_id: string;
    search_enabled?: boolean;
    size_bytes?: number;
    status: string;
    updated_at: string;
    visibility?: string;
};
