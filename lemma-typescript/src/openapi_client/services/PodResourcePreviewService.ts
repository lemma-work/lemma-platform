/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResourcePreviewResponse } from '../models/ResourcePreviewResponse.js';
import type { ResourceType } from '../models/ResourceType.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class PodResourcePreviewService {
    /**
     * Preview a Shared Resource
     * Describe a shared resource, addressed by id or by name.
     *
     * Both, because the two live in different worlds: agents, apps and tables are
     * linked by name, while a document's "name" is its stored path — which a
     * recipient does not have, since the link they were sent carries an id
     * precisely so it does not depend on a path.
     * @param podId
     * @param resourceType
     * @param name
     * @param id
     * @returns ResourcePreviewResponse Successful Response
     * @throws ApiError
     */
    public static podResourcePreview(
        podId: string,
        resourceType: ResourceType,
        name?: (string | null),
        id?: (string | null),
    ): CancelablePromise<ResourcePreviewResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/resources/{resource_type}/preview',
            path: {
                'pod_id': podId,
                'resource_type': resourceType,
            },
            query: {
                'name': name,
                'id': id,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
