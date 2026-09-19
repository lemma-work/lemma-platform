/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type FirstWorkspaceResponse = {
    assistant_id?: (string | null);
    entry: FirstWorkspaceResponse.entry;
    organization_created: boolean;
    organization_id: string;
    pod_created: boolean;
    pod_id?: (string | null);
};
export namespace FirstWorkspaceResponse {
    export enum entry {
        SAVED = 'saved',
        EXISTING = 'existing',
        SURFACE_JOIN = 'surface_join',
        DOMAIN_JOIN = 'domain_join',
        NEW_ORG = 'new_org',
    }
}
