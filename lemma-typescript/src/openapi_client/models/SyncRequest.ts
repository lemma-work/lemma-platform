/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type SyncRequest = {
    direction: SyncRequest.direction;
    machine_path: string;
    pod_id: string;
    /**
     * `to_pod`: the pod folder to put the files in. `from_pod`: the pod file to copy onto the machine.
     */
    pod_path: string;
};
export namespace SyncRequest {
    export enum direction {
        TO_POD = 'to_pod',
        FROM_POD = 'from_pod',
    }
}
