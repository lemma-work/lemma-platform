/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelMessageResponse } from './ChannelMessageResponse.js';
export type ChannelMessagesResponse = {
    /**
     * Whether there are more lines in the direction read: older ones, or with `after_seq`, newer ones.
     */
    has_more: boolean;
    /**
     * Oldest first.
     */
    items: Array<ChannelMessageResponse>;
    /**
     * Pass as `after_seq` for the newer page.
     */
    next_after_seq?: (number | null);
    /**
     * Pass as `before_seq` for the older page.
     */
    next_before_seq?: (number | null);
};
