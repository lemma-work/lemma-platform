/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type FirstWorkspaceResponse = {
    assistant_id?: (string | null);
    entry: FirstWorkspaceResponse.entry;
    organization_created: boolean;
    organization_id: string;
    organization_name: string;
    /**
     * The email domain whose verified addresses may join the organization on their own, when it takes colleagues that way. Its owner can close it in Settings.
     */
    organization_open_to_domain?: (string | null);
    /**
     * The organization's own Lemma, the one everyone in it shares. Differs from `pod_id` when an invitation brought the person to another pod, and is null in an organization that has none.
     */
    organization_pod_id?: (string | null);
    /**
     * Whether the pod in `pod_id` was made by this call.
     */
    pod_created: boolean;
    /**
     * Where the person lands: the pod an invitation named, else the organization's own Lemma, else the private pod `with_pod` asked for. Null when there is none.
     */
    pod_id?: (string | null);
    pod_name?: (string | null);
};
export namespace FirstWorkspaceResponse {
    export enum entry {
        EXISTING = 'existing',
        SURFACE_JOIN = 'surface_join',
        INVITATION = 'invitation',
        DOMAIN_JOIN = 'domain_join',
        NEW_ORG = 'new_org',
    }
}
