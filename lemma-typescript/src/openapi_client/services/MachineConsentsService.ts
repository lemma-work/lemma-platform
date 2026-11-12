/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConsentListResponse } from '../models/ConsentListResponse.js';
import type { ConsentResponse } from '../models/ConsentResponse.js';
import type { GrantConsentRequest } from '../models/GrantConsentRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class MachineConsentsService {
    /**
     * Apps you let run code on your machine
     * @returns ConsentListResponse Successful Response
     * @throws ApiError
     */
    public static machineConsentList(): CancelablePromise<ConsentListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/machine-consents',
        });
    }
    /**
     * Let an app run code on your machine
     * @param requestBody
     * @returns ConsentResponse Successful Response
     * @throws ApiError
     */
    public static machineConsentGrant(
        requestBody: GrantConsentRequest,
    ): CancelablePromise<ConsentResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/machine-consents',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stop an app running code on your machine
     * @param appId
     * @returns void
     * @throws ApiError
     */
    public static machineConsentRevoke(
        appId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/machine-consents/{app_id}',
            path: {
                'app_id': appId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
