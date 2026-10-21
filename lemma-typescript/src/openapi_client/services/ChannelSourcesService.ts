/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SourceSettingsDetailResponse } from '../models/SourceSettingsDetailResponse.js';
import type { SourceSettingsResponse } from '../models/SourceSettingsResponse.js';
import type { SourceUpdateRequest } from '../models/SourceUpdateRequest.js';
import type { TelegramLinkRequest } from '../models/TelegramLinkRequest.js';
import type { TelegramLinkResponse } from '../models/TelegramLinkResponse.js';
import type { WhatsAppGroupRequest } from '../models/WhatsAppGroupRequest.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class ChannelSourcesService {
    /**
     * Telegram Link
     * A one-use link that adds the pod's Telegram bot to a group the caller picks.
     *
     * The group becomes a GROUP channel, the caller's to answer for. Which
     * Telegram account is theirs is still their profile's to say: a link is
     * easily passed on, so the one that used it is never taken for them. The link
     * works for an hour.
     * @param podId
     * @param requestBody
     * @returns TelegramLinkResponse Successful Response
     * @throws ApiError
     */
    public static channelTelegramLinkCreate(
        podId: string,
        requestBody: TelegramLinkRequest,
    ): CancelablePromise<TelegramLinkResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/telegram-links',
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
     * Start Whatsapp Group
     * Start a WhatsApp group with the pod's bot in it, answered for by the caller.
     *
     * It is a new GROUP channel's source: the channel is made with it, and its
     * id is `channel_id`. WhatsApp confirms the group moments later: it comes
     * back ``pending``, and its invite link appears once confirmed. Telegram and
     * Slack cannot create groups for a bot; add the bot to one of theirs instead.
     * The same `Idempotency-Key` from the same member answers with the group it
     * started, for a day.
     * @param podId
     * @param requestBody
     * @param idempotencyKey
     * @returns SourceSettingsResponse Successful Response
     * @throws ApiError
     */
    public static channelWhatsappGroupStart(
        podId: string,
        requestBody: WhatsAppGroupRequest,
        idempotencyKey?: (string | null),
    ): CancelablePromise<SourceSettingsResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/channels/whatsapp-groups',
            path: {
                'pod_id': podId,
            },
            headers: {
                'Idempotency-Key': idempotencyKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                409: `A start with that Idempotency-Key is still running.`,
                422: `Validation Error`,
                429: `The member has started as many groups as a day allows.`,
            },
        });
    }
    /**
     * Get Source
     * A group source's settings: who answers its outsiders, and from what.
     * @param podId
     * @param channelId
     * @param sourceId
     * @returns SourceSettingsDetailResponse Successful Response
     * @throws ApiError
     */
    public static channelSourceGet(
        podId: string,
        channelId: string,
        sourceId: string,
    ): CancelablePromise<SourceSettingsDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/channels/{channel_id}/sources/{source_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'source_id': sourceId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Source
     * Switch outsiders on or off in one group, or take it over (`owner: "me"`).
     * Absent is unchanged; neither can be cleared.
     *
     * The member who answers for the group may; so may anybody who configures the
     * bot when nobody in the pod answers for it, and an admin of the pod, whose
     * change the member is told about.
     * @param podId
     * @param channelId
     * @param sourceId
     * @param requestBody
     * @returns SourceSettingsResponse Successful Response
     * @throws ApiError
     */
    public static channelSourceUpdate(
        podId: string,
        channelId: string,
        sourceId: string,
        requestBody: SourceUpdateRequest,
    ): CancelablePromise<SourceSettingsResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/channels/{channel_id}/sources/{source_id}',
            path: {
                'pod_id': podId,
                'channel_id': channelId,
                'source_id': sourceId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
