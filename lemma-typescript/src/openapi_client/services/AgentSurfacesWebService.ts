/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Accepted } from '../models/Accepted.js';
import type { CodeRequest } from '../models/CodeRequest.js';
import type { HistoryResponse } from '../models/HistoryResponse.js';
import type { MessageRequest } from '../models/MessageRequest.js';
import type { PublicRowsResponse } from '../models/PublicRowsResponse.js';
import type { RowRequest } from '../models/RowRequest.js';
import type { SessionRequest } from '../models/SessionRequest.js';
import type { SessionResponse } from '../models/SessionResponse.js';
import type { TableResponse } from '../models/TableResponse.js';
import type { VerifyRequest } from '../models/VerifyRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentSurfacesWebService {
    /**
     * Web Challenge
     * A proof-of-work to solve before starting a session or asking for a code.
     *
     * ``{"enabled": false}`` when the deployment has bot protection off.
     * @param publicKey
     * @param purpose
     * @returns any Successful Response
     * @throws ApiError
     */
    public static publicWebChallengeRead(
        publicKey: string,
        purpose: string = 'session',
    ): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/public/web/{public_key}/challenge',
            path: {
                'public_key': publicKey,
            },
            query: {
                'purpose': purpose,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Send Code
     * @param publicKey
     * @param requestBody
     * @returns Accepted Successful Response
     * @throws ApiError
     */
    public static publicWebCodeSend(
        publicKey: string,
        requestBody: CodeRequest,
    ): CancelablePromise<Accepted> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/code',
            path: {
                'public_key': publicKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Verify Code
     * Confirm an email address: the session becomes a contact's, under a new
     * secret and a new access token, which replace the old ones.
     * @param publicKey
     * @param requestBody
     * @returns SessionResponse Successful Response
     * @throws ApiError
     */
    public static publicWebCodeVerify(
        publicKey: string,
        requestBody: VerifyRequest,
    ): CancelablePromise<SessionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/code/verify',
            path: {
                'public_key': publicKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Read History
     * @param publicKey
     * @param after
     * @returns HistoryResponse Successful Response
     * @throws ApiError
     */
    public static publicWebHistoryRead(
        publicKey: string,
        after: number = -1,
    ): CancelablePromise<HistoryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/public/web/{public_key}/history',
            path: {
                'public_key': publicKey,
            },
            query: {
                'after': after,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Send Message
     * @param publicKey
     * @param requestBody
     * @returns Accepted Successful Response
     * @throws ApiError
     */
    public static publicWebMessageSend(
        publicKey: string,
        requestBody: MessageRequest,
    ): CancelablePromise<Accepted> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/messages',
            path: {
                'public_key': publicKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Read Rows
     * Read a table the pod marked Public: every row, at most 500.
     *
     * The same reading the pod's chat does for this visitor -- Public, and
     * nothing else. A booking page reads its free slots here.
     * @param publicKey
     * @param table
     * @param orderBy
     * @param desc
     * @returns PublicRowsResponse Successful Response
     * @throws ApiError
     */
    public static publicWebRowsRead(
        publicKey: string,
        table: string,
        orderBy?: (string | null),
        desc: boolean = false,
    ): CancelablePromise<PublicRowsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/public/web/{public_key}/rows',
            path: {
                'public_key': publicKey,
            },
            query: {
                'table': table,
                'order_by': orderBy,
                'desc': desc,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Add Row
     * Add one row to a table the pod opened to visitors. Nothing is read back.
     * @param publicKey
     * @param requestBody
     * @returns Accepted Successful Response
     * @throws ApiError
     */
    public static publicWebRowAdd(
        publicKey: string,
        requestBody: RowRequest,
    ): CancelablePromise<Accepted> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/rows',
            path: {
                'public_key': publicKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Start Session
     * Start a session, or come back to one: either way, a new access token.
     * @param publicKey
     * @param requestBody
     * @returns SessionResponse Successful Response
     * @throws ApiError
     */
    public static publicWebSessionStart(
        publicKey: string,
        requestBody: SessionRequest,
    ): CancelablePromise<SessionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/public/web/{public_key}/session',
            path: {
                'public_key': publicKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Stream Answers
     * The bot's answer as it is written, one JSON object per line.
     *
     * The first line is ``{"type": "open"}``, a handshake that says the stream is
     * up, not a frame of the conversation. Only what a visitor may see follows:
     * see ``agent.contracts.visitor_stream``. The page reconnects when it closes.
     * A session holds at most two streams at once.
     * @param publicKey
     * @returns any Successful Response
     * @throws ApiError
     */
    public static publicWebStreamRead(
        publicKey: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/public/web/{public_key}/stream',
            path: {
                'public_key': publicKey,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Web Read Table
     * What a page may ask for on a table the pod opened to visitors.
     *
     * The open columns only, in order: enough to draw a form, and nothing else
     * about the table or its rows.
     * @param publicKey
     * @param table
     * @returns TableResponse Successful Response
     * @throws ApiError
     */
    public static publicWebTableRead(
        publicKey: string,
        table: string,
    ): CancelablePromise<TableResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/public/web/{public_key}/table',
            path: {
                'public_key': publicKey,
            },
            query: {
                'table': table,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
