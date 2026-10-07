/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AskablePodListResponse } from '../models/AskablePodListResponse.js';
import type { ConnectPodRequest } from '../models/ConnectPodRequest.js';
import type { PodLinkListResponse } from '../models/PodLinkListResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentAsksService {
    /**
     * List Pods This Pod Can Ask
     * The other pods this pod's assistant can ask: those the caller is also a member of, which it asks as the caller, and those connected to this pod, which it can ask with nobody present.
     * @param podId
     * @returns AskablePodListResponse Successful Response
     * @throws ApiError
     */
    public static agentAskablePodList(
        podId: string,
    ): CancelablePromise<AskablePodListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/askable-pods',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Pods That Can Ask This Pod
     * The other pods connected to this one: each may ask this pod's assistant with nobody present, and read what is Public here plus what this pod shared with it.
     * @param podId
     * @returns PodLinkListResponse Successful Response
     * @throws ApiError
     */
    public static agentPodLinkList(
        podId: string,
    ): CancelablePromise<PodLinkListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/pod-links',
            path: {
                'pod_id': podId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Disconnect A Pod From This One
     * Stop another pod asking this one, and take back what it was shared.
     * @param podId
     * @param askingPodId
     * @returns void
     * @throws ApiError
     */
    public static agentPodLinkDisconnect(
        podId: string,
        askingPodId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/pod-links/{asking_pod_id}',
            path: {
                'pod_id': podId,
                'asking_pod_id': askingPodId,
            },
            errors: {
                404: `That pod is not connected to this one`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Connect A Pod To This One
     * Let another pod in this organization ask this one, sharing the tables and folders named for reading. Connecting again replaces what is shared. Needs pod.member.manage here, and membership of the other pod; the caller looks after the link.
     * @param podId
     * @param askingPodId
     * @param requestBody
     * @returns void
     * @throws ApiError
     */
    public static agentPodLinkConnect(
        podId: string,
        askingPodId: string,
        requestBody: ConnectPodRequest,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/pod-links/{asking_pod_id}',
            path: {
                'pod_id': podId,
                'asking_pod_id': askingPodId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Something named can't be shared, or not for that`,
                409: `The two pods can't be connected`,
                422: `Validation Error`,
            },
        });
    }
}
