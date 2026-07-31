/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type FileDetailResponse = {
    allowed_actions?: Array<string>;
    content_sha256?: (string | null);
    created_at: string;
    description: (string | null);
    id: string;
    indexed_at?: (string | null);
    kind: string;
    last_processing_error?: (string | null);
    metadata?: (Record<string, any> | null);
    mime_type?: (string | null);
    name: string;
    owner_user_id?: (string | null);
    path: string;
    pod_id: string;
    processing_attempts?: number;
    search_enabled?: boolean;
    size_bytes?: number;
    status: string;
    updated_at: string;
    visibility?: string;
};
