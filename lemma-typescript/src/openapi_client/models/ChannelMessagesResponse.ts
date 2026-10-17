/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelMessageResponse } from './ChannelMessageResponse.js';
export type ChannelMessagesResponse = {
    /**
     * Oldest first, the newest page unless `before` was given.
     */
    items: Array<ChannelMessageResponse>;
    /**
     * Pass as `before` for the page before this one.
     */
    next_before?: (string | null);
};
