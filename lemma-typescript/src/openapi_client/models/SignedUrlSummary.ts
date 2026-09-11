/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One live public link, as its pod sees it.
 *
 * Deliberately carries the ``code`` and not the full URL: this is the listing
 * a pod member reads to decide what to revoke, and the code is what revoking
 * takes. Anyone who needs the openable URL already has it.
 */
export type SignedUrlSummary = {
    code: string;
    content_type: string;
    created_at?: (string | null);
    exhausted_at?: (string | null);
    expires_at: string;
    filename: string;
    max_hits: number;
    path: string;
    revoked_at?: (string | null);
    size_bytes: number;
};
