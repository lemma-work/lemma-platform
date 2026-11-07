/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PreviewResponse = {
    created_at: string;
    expires_at?: (string | null);
    id: string;
    port: number;
    revoked_at?: (string | null);
    url: string;
    visibility: PreviewResponse.visibility;
};
export namespace PreviewResponse {
    export enum visibility {
        PRIVATE = 'private',
        LINK = 'link',
    }
}
