/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelDetailResponse } from '../models/ChannelDetailResponse.js';
import type { ChannelKind } from '../models/ChannelKind.js';
import type { ChannelListResponse } from '../models/ChannelListResponse.js';
import type { ChannelMessagesResponse } from '../models/ChannelMessagesResponse.js';
import type { ChannelResponse } from '../models/ChannelResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class ChannelsService {
    /**
     * List Channels
     * The pod's channels this member sees, most recently active first.
     * @param podId
     * @param kind
     * @param limit
     * @param before
     * @returns ChannelListResponse Successful Response
     * @throws ApiError
     */
    public static channelList(
        podId: string,
        kind?: (ChannelKind | null),
        limit: number = 50,
        before?: (string | null),
    ): CancelablePromise<ChannelListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels',
            path: {
                'pod_id': podId,
            },
            query: {
                'kind': kind,
                'limit': limit,
                'before': before,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Channel
     * One channel, with the places it is fed from and the people in it.
     * @param podId
     * @param channelId
     * @returns ChannelDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelGet(
        podId: string,
        channelId: string,
    ): CancelablePromise<ChannelDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Release Channel
     * Hand the contact back to the channel's agents.
     * @param podId
     * @param channelId
     * @returns ChannelResponse Successful Response
     * @throws ApiError
     */
    public static channelHandlerRelease(
        podId: string,
        channelId: string,
    ): CancelablePromise<ChannelResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/channels/{channel_id}/handler',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Take Channel
     * Answer a contact by hand: their agent stays quiet until it is handed back.
     * @param podId
     * @param channelId
     * @returns ChannelResponse Successful Response
     * @throws ApiError
     */
    public static channelHandlerTake(
        podId: string,
        channelId: string,
    ): CancelablePromise<ChannelResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/pods/{pod_id}/channels/{channel_id}/handler',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Channel Messages
     * The channel's timeline: who said what, when and where, oldest first.
     * @param podId
     * @param channelId
     * @param limit
     * @param before
     * @returns ChannelMessagesResponse Successful Response
     * @throws ApiError
     */
    public static channelMessageList(
        podId: string,
        channelId: string,
        limit: number = 50,
        before?: (string | null),
    ): CancelablePromise<ChannelMessagesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/messages',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            query: {
                'limit': limit,
                'before': before,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Mark Channel Read
     * Everything in the channel up to now has been read by this member.
     * @param podId
     * @param channelId
     * @returns void
     * @throws ApiError
     */
    public static channelMarkRead(
        podId: string,
        channelId: string,
    ): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/{channel_id}/read',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
