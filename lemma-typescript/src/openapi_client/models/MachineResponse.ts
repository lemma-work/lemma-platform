/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MachineCapabilitiesModel } from './MachineCapabilitiesModel.js';
export type MachineResponse = {
    /**
     * What the caller may do with it.
     */
    access: MachineResponse.access;
    capabilities: MachineCapabilitiesModel;
    /**
     * Compute used this calendar month (1 CU = one hour at 2x4).
     */
    compute_units_this_month?: (number | null);
    display_name: string;
    fabric: string;
    home: string;
    id: string;
    kind: MachineResponse.kind;
    last_used_at?: (string | null);
    name: string;
    pod_id?: (string | null);
    /**
     * How to name this machine in a call: `me`, `pod:<pod-id>/<name>` or `mine:<name>`. In a URL path use `:` for the slash.
     */
    ref: string;
    run_slots: number;
    /**
     * `<vcpu>x<memory GB>`, e.g. `2x4`.
     */
    size: string;
    status: MachineResponse.status;
    template: MachineResponse.template;
    visibility: MachineResponse.visibility;
};
export namespace MachineResponse {
    /**
     * What the caller may do with it.
     */
    export enum access {
        STATUS = 'status',
        USE = 'use',
        MANAGE = 'manage',
    }
    export enum kind {
        DEFAULT = 'default',
        POD = 'pod',
        CONNECTED = 'connected',
    }
    export enum status {
        RUNNING = 'running',
        STARTING = 'starting',
        STOPPED = 'stopped',
        OFFLINE = 'offline',
        ERROR = 'error',
        DELETED = 'deleted',
    }
    export enum template {
        WORKSPACE = 'workspace',
        WORKSPACE_DOCKER = 'workspace-docker',
    }
    export enum visibility {
        PRIVATE = 'private',
        POD = 'pod',
        RESTRICTED = 'restricted',
    }
}
