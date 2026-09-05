/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One entry in an app's release history.
 */
export type AppReleaseResponse = {
    app_id: string;
    created_at: (string | null);
    created_by?: (string | null);
    /**
     * Whether this release's own source archive is still stored.
     */
    has_source: boolean;
    id: string;
    /**
     * True for the release this app currently serves.
     */
    is_live: boolean;
    label?: (string | null);
    readonly preview_url: string;
    /**
     * Set when retention removed this release's build. The entry stays in the history, but it can no longer be previewed or promoted.
     */
    pruned_at?: (string | null);
    release_number: number;
    /**
     * sha256 digest of the release's dist archive.
     */
    version: string;
};
