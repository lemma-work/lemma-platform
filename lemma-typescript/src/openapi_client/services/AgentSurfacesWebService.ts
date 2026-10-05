/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentSurfacesWebService {
    /**
     * Web Send Code
     * @param publicKey
     * @returns any Successful Response
     * @throws ApiError
     */
    public static publicWebCodeSend(
        publicKey: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/code',
            path: {
                'public_key': publicKey,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Verify Code
     * @param publicKey
     * @returns any Successful Response
     * @throws ApiError
     */
    public static publicWebCodeVerify(
        publicKey: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/code/verify',
            path: {
                'public_key': publicKey,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Read History
     * @param publicKey
     * @returns any Successful Response
     * @throws ApiError
     */
    public static publicWebHistoryRead(
        publicKey: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/history',
            path: {
                'public_key': publicKey,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Send Message
     * @param publicKey
     * @returns any Successful Response
     * @throws ApiError
     */
    public static publicWebMessageSend(
        publicKey: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/messages',
            path: {
                'public_key': publicKey,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Start Session
     * @param publicKey
     * @returns any Successful Response
     * @throws ApiError
     */
    public static publicWebSessionStart(
        publicKey: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/session',
            path: {
                'public_key': publicKey,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Submit Form
     * @param publicKey
     * @returns any Successful Response
     * @throws ApiError
     */
    public static publicWebFormSubmit(
        publicKey: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/submit',
            path: {
                'public_key': publicKey,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
