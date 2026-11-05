/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutreachRequest } from '../models/OutreachRequest.js';
import type { OutreachResponse } from '../models/OutreachResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class ContactOutreachService {
    /**
     * Send Outreach
     * Write to a contact first, on one of their handles: email any time,
     * WhatsApp within 24 hours of their last message there. The line goes in
     * their channel, at that handle's place -- opened if new -- and is sent from
     * there. The same `client_message_id`, or `Idempotency-Key`, from the same
     * member is the same line: a retry sends nothing new.
     * @param podId
     * @param contactId
     * @param requestBody
     * @param idempotencyKey
     * @returns OutreachResponse Successful Response
     * @throws ApiError
     */
    public static contactOutreachSend(
        podId: string,
        contactId: string,
        requestBody: OutreachRequest,
        idempotencyKey?: (string | null),
    ): CancelablePromise<OutreachResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/contacts/{contact_id}/outreach',
            path: {
                'pod_id': podId,
                'contact_id': contactId,
            },
            headers: {
                'Idempotency-Key': idempotencyKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                404: `No such contact.`,
                409: `They asked not to be written to there, or the pod has nowhere to write from.`,
                422: `Not one of their handles, or not one that may be written to first now.`,
            },
        });
    }
}
