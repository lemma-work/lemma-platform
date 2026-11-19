/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreatePreviewRequest = {
    port: number;
    /**
     * How long it lives. Required-ish for `link`: capped at 24 hours.
     */
    ttl_seconds?: (number | null);
    /**
     * `private`: people who can see the machine, after signing in. `link`: anyone with the URL, for at most 24 hours.
     */
    visibility?: CreatePreviewRequest.visibility;
};
export namespace CreatePreviewRequest {
    /**
     * `private`: people who can see the machine, after signing in. `link`: anyone with the URL, for at most 24 hours.
     */
    export enum visibility {
        PRIVATE = 'private',
        LINK = 'link',
    }
}
