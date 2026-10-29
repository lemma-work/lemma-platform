/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MachineFileEntry } from '../models/MachineFileEntry.js';
import type { MachineFileListResponse } from '../models/MachineFileListResponse.js';
import type { MoveRequest } from '../models/MoveRequest.js';
import type { SyncRequest } from '../models/SyncRequest.js';
import type { SyncResponse } from '../models/SyncResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class MachineFilesService {
    /**
     * Delete a file or directory on a machine
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param path
     * @param recursive
     * @returns void
     * @throws ApiError
     */
    public static machineFilesDelete(
        ref: string,
        path: string,
        recursive: boolean = false,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/machines/{ref}/files',
            path: {
                'ref': ref,
            },
            query: {
                'path': path,
                'recursive': recursive,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List a directory on a machine
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param path
     * @param wake Start the machine if it is paused.
     * @param after
     * @returns MachineFileListResponse Successful Response
     * @throws ApiError
     */
    public static machineFilesList(
        ref: string,
        path?: (string | null),
        wake: boolean = false,
        after?: (string | null),
    ): CancelablePromise<MachineFileListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/files',
            path: {
                'ref': ref,
            },
            query: {
                'path': path,
                'wake': wake,
                'after': after,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Write a file on a machine
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param path
     * @returns MachineFileEntry Successful Response
     * @throws ApiError
     */
    public static machineFilesWrite(
        ref: string,
        path: string,
    ): CancelablePromise<MachineFileEntry> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/machines/{ref}/files/content',
            path: {
                'ref': ref,
            },
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read a file on a machine
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param path
     * @param offset
     * @param length
     * @param range
     * @param ifNoneMatch
     * @returns any Successful Response
     * @throws ApiError
     */
    public static machineFilesContent(
        ref: string,
        path: string,
        offset?: number,
        length?: (number | null),
        range?: (string | null),
        ifNoneMatch?: (string | null),
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/files:content',
            path: {
                'ref': ref,
            },
            headers: {
                'Range': range,
                'If-None-Match': ifNoneMatch,
            },
            query: {
                'path': path,
                'offset': offset,
                'length': length,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Move or rename a path on a machine
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns MachineFileEntry Successful Response
     * @throws ApiError
     */
    public static machineFilesMove(
        ref: string,
        requestBody: MoveRequest,
    ): CancelablePromise<MachineFileEntry> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/files:move',
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
     * Stat one path on a machine
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param path
     * @returns MachineFileEntry Successful Response
     * @throws ApiError
     */
    public static machineFilesStat(
        ref: string,
        path: string,
    ): CancelablePromise<MachineFileEntry> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/files:stat',
            path: {
                'ref': ref,
            },
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Copy files between a machine and pod files
     * Server-side, so a render's output or a pod's input does not make a round
     * trip through whoever asked. ``to_pod`` copies a file, or a directory's files
     * (at most 500, 256 MB), into a pod folder; ``from_pod`` copies one pod file
     * onto the machine.
     * @param ref Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in context), `mine:<name>`, or its id.
     * @param requestBody
     * @returns SyncResponse Successful Response
     * @throws ApiError
     */
    public static machineFilesSync(
        ref: string,
        requestBody: SyncRequest,
    ): CancelablePromise<SyncResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machines/{ref}/files:sync',
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
}
