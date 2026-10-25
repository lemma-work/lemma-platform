/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type HomeSeenResponse = {
    /**
     * What it was before this write. On a read, the same as `seen_at`.
     */
    previous_seen_at?: (string | null);
    /**
     * When the member last looked at Home; null until first marked.
     */
    seen_at?: (string | null);
};
