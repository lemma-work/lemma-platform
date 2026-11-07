/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CreatePodMachineRequest = {
    display_name?: (string | null);
    name: string;
    run_slots?: number;
    /**
     * Defaults to the plan's default size.
     */
    size?: (string | null);
    template?: CreatePodMachineRequest.template;
    visibility?: CreatePodMachineRequest.visibility;
};
export namespace CreatePodMachineRequest {
    export enum template {
        WORKSPACE = 'workspace',
        WORKSPACE_DOCKER = 'workspace-docker',
    }
    export enum visibility {
        POD = 'pod',
        RESTRICTED = 'restricted',
    }
}
