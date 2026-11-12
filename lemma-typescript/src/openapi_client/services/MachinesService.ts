/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CreatePodMachineRequest } from '../models/CreatePodMachineRequest.js';
import type { MachineGrantsModel } from '../models/MachineGrantsModel.js';
import type { MachineListResponse } from '../models/MachineListResponse.js';
import type { MachinePolicyResponse } from '../models/MachinePolicyResponse.js';
import type { MachineResponse } from '../models/MachineResponse.js';
import type { MachineStartupResponse } from '../models/MachineStartupResponse.js';
import type { ResetRequest } from '../models/ResetRequest.js';
import type { ResetResponse } from '../models/ResetResponse.js';
import type { UpdatePodMachineRequest } from '../models/UpdatePodMachineRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class MachinesService {
    /**
     * List the machines you can see
     * @param podId Also list this pod's machines.
     * @returns MachineListResponse Successful Response
     * @throws ApiError
     */
    public static machineList(
        podId?: (string | null),
    ): CancelablePromise<MachineListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines',
            query: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get one machine
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns MachineResponse Successful Response
     * @throws ApiError
     */
    public static machineGet(
        ref: string,
    ): CancelablePromise<MachineResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}',
            path: {
                'ref': ref,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Whether a machine is ready, without waking it
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns MachineStartupResponse Successful Response
     * @throws ApiError
     */
    public static machineStartup(
        ref: string,
    ): CancelablePromise<MachineStartupResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/startup',
            path: {
                'ref': ref,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stop a machine's compute, keeping its disk
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns MachineResponse Successful Response
     * @throws ApiError
     */
    public static machineRelease(
        ref: string,
    ): CancelablePromise<MachineResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}:release',
            path: {
                'ref': ref,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Clean a machine by scope
     * @param ref
     * @param requestBody
     * @returns ResetResponse Successful Response
     * @throws ApiError
     */
    public static machineReset(
        ref: string,
        requestBody: ResetRequest,
    ): CancelablePromise<ResetResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}:reset',
            path: {
                'ref': ref,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stop a machine and start it again
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns MachineResponse Successful Response
     * @throws ApiError
     */
    public static machineRestart(
        ref: string,
    ): CancelablePromise<MachineResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}:restart',
            path: {
                'ref': ref,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Which machines you or a pod may create
     * @param podId
     * @returns MachinePolicyResponse Successful Response
     * @throws ApiError
     */
    public static machinePolicy(
        podId?: (string | null),
    ): CancelablePromise<MachinePolicyResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines:policy',
            query: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create a machine the pod's agents share
     * @param podId
     * @param requestBody
     * @returns MachineResponse Successful Response
     * @throws ApiError
     */
    public static machinePodCreate(
        podId: string,
        requestBody: CreatePodMachineRequest,
    ): CancelablePromise<MachineResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/machines',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete a pod machine and its disk
     * @param podId
     * @param name
     * @returns void
     * @throws ApiError
     */
    public static machinePodDelete(
        podId: string,
        name: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/machines/{name}',
            path: {
                'pod_id': podId,
                'name': name,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rename, resize or change who sees a pod machine
     * @param podId
     * @param name
     * @param requestBody
     * @returns MachineResponse Successful Response
     * @throws ApiError
     */
    public static machinePodUpdate(
        podId: string,
        name: string,
        requestBody: UpdatePodMachineRequest,
    ): CancelablePromise<MachineResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/machines/{name}',
            path: {
                'pod_id': podId,
                'name': name,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Who is granted a pod machine
     * @param podId
     * @param name
     * @returns MachineGrantsModel Successful Response
     * @throws ApiError
     */
    public static machinePodGrantsGet(
        podId: string,
        name: string,
    ): CancelablePromise<MachineGrantsModel> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/machines/{name}/grants',
            path: {
                'pod_id': podId,
                'name': name,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Replace who is granted a pod machine
     * @param podId
     * @param name
     * @param requestBody
     * @returns MachineGrantsModel Successful Response
     * @throws ApiError
     */
    public static machinePodGrantsSet(
        podId: string,
        name: string,
        requestBody: MachineGrantsModel,
    ): CancelablePromise<MachineGrantsModel> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/machines/{name}/grants',
            path: {
                'pod_id': podId,
                'name': name,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
