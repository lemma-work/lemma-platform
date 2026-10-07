/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionResponse } from '../models/DecisionResponse.js';
import type { MakeDecisionRequest } from '../models/MakeDecisionRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class DecisionsService {
    /**
     * Make a decision
     * Answer closed questions -- a choice, several choices, yes or no, a point on a scale -- about one piece of evidence. Nothing is stored: record the answer wherever it matters to you. An answer of null means the evidence did not support one. 422 means the request cannot be asked as sent; 429 and 503 mean ask again later.
     * @param podId
     * @param requestBody
     * @returns DecisionResponse Successful Response
     * @throws ApiError
     */
    public static decisionMake(
        podId: string,
        requestBody: MakeDecisionRequest,
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
                413: `The request body is too large.`,
                422: `The questions, evidence or examples are not valid.`,
                429: `Rate or spend limit reached; see Retry-After.`,
                503: `The decision provider did not answer; retry.`,
            },
        });
    }
}
