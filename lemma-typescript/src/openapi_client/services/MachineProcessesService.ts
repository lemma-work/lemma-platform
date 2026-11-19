/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProcessInputRequest } from '../models/ProcessInputRequest.js';
import type { ProcessListResponse } from '../models/ProcessListResponse.js';
import type { ProcessOutputResponse } from '../models/ProcessOutputResponse.js';
import type { ProcessResponse } from '../models/ProcessResponse.js';
import type { StartProcessRequest } from '../models/StartProcessRequest.js';
import type { TerminalSizeModel } from '../models/TerminalSizeModel.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class MachineProcessesService {
    /**
     * What is running on a machine
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns ProcessListResponse Successful Response
     * @throws ApiError
     */
    public static machineProcessList(
        ref: string,
    ): CancelablePromise<ProcessListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/processes',
            path: {
                'ref': ref,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Start a process on a machine
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns ProcessResponse Successful Response
     * @throws ApiError
     */
    public static machineProcessStart(
        ref: string,
        requestBody: StartProcessRequest,
    ): CancelablePromise<ProcessResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/processes',
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
     * Stop a process
     * @param processId
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns void
     * @throws ApiError
     */
    public static machineProcessKill(
        processId: string,
        ref: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/machines/{ref}/processes/{process_id}',
            path: {
                'process_id': processId,
                'ref': ref,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * One process
     * @param processId
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @returns ProcessResponse Successful Response
     * @throws ApiError
     */
    public static machineProcessGet(
        processId: string,
        ref: string,
    ): CancelablePromise<ProcessResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/processes/{process_id}',
            path: {
                'process_id': processId,
                'ref': ref,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * A page of a process's output
     * @param processId
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param after Return output after this sequence.
     * @param waitSeconds Wait this long for new output.
     * @returns ProcessOutputResponse Successful Response
     * @throws ApiError
     */
    public static machineProcessOutput(
        processId: string,
        ref: string,
        after?: number,
        waitSeconds?: number,
    ): CancelablePromise<ProcessOutputResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/processes/{process_id}/output',
            path: {
                'process_id': processId,
                'ref': ref,
            },
            query: {
                'after': after,
                'wait_seconds': waitSeconds,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Type into a process
     * @param processId
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns void
     * @throws ApiError
     */
    public static machineProcessInput(
        processId: string,
        ref: string,
        requestBody: ProcessInputRequest,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/processes/{process_id}:input',
            path: {
                'process_id': processId,
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
     * Resize a process's terminal
     * @param processId
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns void
     * @throws ApiError
     */
    public static machineProcessResize(
        processId: string,
        ref: string,
        requestBody: TerminalSizeModel,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/processes/{process_id}:resize',
            path: {
                'process_id': processId,
                'ref': ref,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
