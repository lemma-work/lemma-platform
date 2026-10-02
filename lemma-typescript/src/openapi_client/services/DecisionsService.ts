/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AnswerBody } from '../models/AnswerBody.js';
import type { CreateDeciderBody } from '../models/CreateDeciderBody.js';
import type { DecideBody } from '../models/DecideBody.js';
import type { DeciderListResponse } from '../models/DeciderListResponse.js';
import type { DecideRowsBody } from '../models/DecideRowsBody.js';
import type { DeciderResponse } from '../models/DeciderResponse.js';
import type { DeciderTestBody } from '../models/DeciderTestBody.js';
import type { DeciderTestResponse } from '../models/DeciderTestResponse.js';
import type { DeciderVersionListResponse } from '../models/DeciderVersionListResponse.js';
import type { DecisionListResponse } from '../models/DecisionListResponse.js';
import type { DecisionResponse } from '../models/DecisionResponse.js';
import type { RowsResponse } from '../models/RowsResponse.js';
import type { UpdateDeciderBody } from '../models/UpdateDeciderBody.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class DecisionsService {
    /**
     * List deciders
     * @param podId
     * @param limit
     * @returns DeciderListResponse Successful Response
     * @throws ApiError
     */
    public static deciderList(
        podId: string,
        limit: number = 100,
    ): CancelablePromise<DeciderListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/deciders',
            path: {
                'pod_id': podId,
            },
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create a decider
     * Define a named decider: its questions, guidance, input view, rules and policy. The response lists anything the definition allows but that is usually a mistake.
     * @param podId
     * @param requestBody
     * @returns DeciderResponse Successful Response
     * @throws ApiError
     */
    public static deciderCreate(
        podId: string,
        requestBody: CreateDeciderBody,
    ): CancelablePromise<DeciderResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/deciders',
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
     * Test a decider
     * Decide sample rows with a saved decider or a draft definition, without recording anything. Rows that carry expected answers are compared with what the decider said.
     * @param podId
     * @param requestBody
     * @returns DeciderTestResponse Successful Response
     * @throws ApiError
     */
    public static deciderTest(
        podId: string,
        requestBody: DeciderTestBody,
    ): CancelablePromise<DeciderTestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/deciders/test',
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
     * Delete a decider
     * Delete a decider, its versions and what it learned. Its decisions stay, naming the decider they were asked of.
     * @param podId
     * @param deciderName
     * @returns void
     * @throws ApiError
     */
    public static deciderDelete(
        podId: string,
        deciderName: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/deciders/{decider_name}',
            path: {
                'pod_id': podId,
                'decider_name': deciderName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get a decider
     * @param podId
     * @param deciderName
     * @returns DeciderResponse Successful Response
     * @throws ApiError
     */
    public static deciderGet(
        podId: string,
        deciderName: string,
    ): CancelablePromise<DeciderResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/deciders/{decider_name}',
            path: {
                'pod_id': podId,
                'decider_name': deciderName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Save a new version of a decider
     * Replace a decider's definition. The old version is kept, and decisions made with it still name it.
     * @param podId
     * @param deciderName
     * @param requestBody
     * @returns DeciderResponse Successful Response
     * @throws ApiError
     */
    public static deciderUpdate(
        podId: string,
        deciderName: string,
        requestBody: UpdateDeciderBody,
    ): CancelablePromise<DeciderResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/deciders/{decider_name}',
            path: {
                'pod_id': podId,
                'decider_name': deciderName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List a decider's versions
     * @param podId
     * @param deciderName
     * @param limit
     * @returns DeciderVersionListResponse Successful Response
     * @throws ApiError
     */
    public static deciderVersionList(
        podId: string,
        deciderName: string,
        limit: number = 50,
    ): CancelablePromise<DeciderVersionListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/deciders/{decider_name}/versions',
            path: {
                'pod_id': podId,
                'decider_name': deciderName,
            },
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List decisions
     * Decisions in this pod, newest first: the ones shared with the pod and your own.
     * @param podId
     * @param decider Only this decider's decisions.
     * @param openOnly Only decisions with a question left open.
     * @param before Only decisions made before this time.
     * @param limit
     * @returns DecisionListResponse Successful Response
     * @throws ApiError
     */
    public static decisionList(
        podId: string,
        decider?: (string | null),
        openOnly: boolean = false,
        before?: (string | null),
        limit: number = 50,
    ): CancelablePromise<DecisionListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/decisions',
            path: {
                'pod_id': podId,
            },
            query: {
                'decider': decider,
                'open_only': openOnly,
                'before': before,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Ask a decision
     * Ask a decider -- a pod decider by name, `system:<name>`, or questions passed inline -- about one state. With a `subject`, a decider is asked once: asking again returns the recorded decision.
     * @param podId
     * @param requestBody
     * @returns DecisionResponse Successful Response
     * @throws ApiError
     */
    public static decisionCreate(
        podId: string,
        requestBody: DecideBody,
    ): CancelablePromise<DecisionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/decisions',
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
     * Decide many rows
     * Ask one decider about many rows at once, for sorting a list rather than one event. Rows are decided in parallel within the bulk budget.
     * @param podId
     * @param requestBody
     * @returns RowsResponse Successful Response
     * @throws ApiError
     */
    public static decisionRows(
        podId: string,
        requestBody: DecideRowsBody,
    ): CancelablePromise<RowsResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/decisions/rows',
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
     * Get a decision
     * @param podId
     * @param decisionId
     * @returns DecisionResponse Successful Response
     * @throws ApiError
     */
    public static decisionGet(
        podId: string,
        decisionId: string,
    ): CancelablePromise<DecisionResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/decisions/{decision_id}',
            path: {
                'pod_id': podId,
                'decision_id': decisionId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Answer a decision
     * Answer questions a decision left open, or correct a machine's answer. A person's answer becomes an example the decider learns from.
     * @param podId
     * @param decisionId
     * @param requestBody
     * @returns DecisionResponse Successful Response
     * @throws ApiError
     */
    public static decisionAnswer(
        podId: string,
        decisionId: string,
        requestBody: AnswerBody,
    ): CancelablePromise<DecisionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/decisions/{decision_id}/answer',
            path: {
                'pod_id': podId,
                'decision_id': decisionId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
