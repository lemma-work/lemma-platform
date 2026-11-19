/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ForgetResponse } from '../models/ForgetResponse.js';
import type { WebLoginListResponse } from '../models/WebLoginListResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class MachineLoginsService {
    /**
     * The sites a machine's browser is signed in to
     * @param ref
     * @param wake Start the machine if it is paused.
     * @returns WebLoginListResponse Successful Response
     * @throws ApiError
     */
    public static machineBrowserLoginsList(
        ref: string,
        wake: boolean = false,
    ): CancelablePromise<WebLoginListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machines/{ref}/browser/logins',
            path: {
                'ref': ref,
            },
            query: {
                'wake': wake,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Sign a machine's browser out of a site
     * Explicit and per site: the only way a login is removed from a machine
     * short of a confirmed `home` reset.
     * @param ref
     * @param site
     * @returns ForgetResponse Successful Response
     * @throws ApiError
     */
    public static machineBrowserLoginsForget(
        ref: string,
        site: string,
    ): CancelablePromise<ForgetResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/machines/{ref}/browser/logins/{site}',
            path: {
                'ref': ref,
                'site': site,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
